#!/usr/bin/env python3
"""
Books in Books - Flask Web Server
Serves the main HTML page and JSON API endpoints.
"""

from flask import Flask, render_template, jsonify, request, session
import sqlite3
import threading
import copy
import json
import re
import hmac
import ipaddress
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse
from functools import wraps
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024  # 80 MB
# No built-in secrets: a random session key per start unless one is configured,
# and no default admin token. Without ADMIN_TOKEN, only this machine is admin.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
ADMIN_TOKEN = (os.environ.get("ADMIN_TOKEN") or "").strip() or None

ALLOWED_EXTENSIONS = {".epub", ".txt"}
BOOKS_DIR = Path("books")
STATUS_FILE = Path("process_status.json")

# Thread-safe processing status for UI polling (also persisted to disk)
_process_lock = threading.RLock()
_process_thread = None
_process_job_args = {"only_filenames": None, "force": False}
_process_status = {
    "status": "idle",
    "phase": "idle",
    "current_file": None,
    "processed": 0,
    "total": 0,
    "skipped": 0,
    "books_processed": [],
    "errors": [],
    "message": "Ready",
    "updated_at": None,
}


def _load_process_status():
    global _process_status
    if not STATUS_FILE.exists():
        return
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    # Previous run claimed running but this process just started — worker is gone
    if data.get("status") == "running":
        data["status"] = "error"
        data["phase"] = "idle"
        data["message"] = (
            "Borges lost his place (server restarted). "
            "Click Ask Borges & Lucien to continue."
        )
        errors = list(data.get("errors") or [])
        errors.append("Interrupted by server restart")
        data["errors"] = errors
    _process_status.update(data)


def _persist_process_status(payload):
    """Write status to disk (safe to call outside the lock)."""
    try:
        STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _save_process_status():
    """Persist current in-memory status (caller may already hold the lock)."""
    _persist_process_status(copy.deepcopy(_process_status))


def _worker_alive():
    return _process_thread is not None and _process_thread.is_alive()


def _mark_interrupted_locked(
    reason="Borges lost his place. Click Ask Borges & Lucien to continue.",
):
    errors = list(_process_status.get("errors") or [])
    if reason not in errors:
        errors.append(reason)
    _process_status.update(
        {
            "status": "error",
            "phase": "idle",
            "message": reason,
            "errors": errors,
            "updated_at": time.time(),
        }
    )


def _reconcile_process_status_locked():
    """If UI thinks we're running but the worker thread is dead, heal the state."""
    if _process_status.get("status") != "running":
        return False
    if _worker_alive():
        return False
    _mark_interrupted_locked(
        "Borges lost his place (worker stopped). Click Ask Borges & Lucien to continue."
    )
    return True


_load_process_status()


def book_cover_url(book_row, size="M"):
    """Prefer stored external cover_url; else Open Library cover_id."""
    keys = book_row.keys() if hasattr(book_row, "keys") else []
    if "cover_url" in keys and book_row["cover_url"]:
        return book_row["cover_url"]
    cover_id = None
    if isinstance(book_row, dict):
        if book_row.get("cover_url"):
            return book_row["cover_url"]
        cover_id = book_row.get("cover_id")
    elif "cover_id" in keys:
        cover_id = book_row["cover_id"]
    if cover_id:
        return f"https://covers.openlibrary.org/b/id/{cover_id}-{size}.jpg"
    return None


def get_db_connection():
    """Create a database connection."""
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def extract_title_from_filename(filename: str) -> str:
    """Extract title from filename (handles both 'Author - Title' and 'Title - Author' formats)."""
    from process_books import extract_title_and_author

    title, _author = extract_title_and_author(filename)
    return title


def parse_filename_parts(stem: str):
    """Return (title, author) from a filename stem."""
    from process_books import extract_title_and_author

    return extract_title_and_author(stem)


def file_identity(file_path: Path):
    """(title, author) for a library file: EPUB metadata first, filename second."""
    from process_books import file_title_and_author

    return file_title_and_author(file_path)


def owned_book_files():
    """{book id: filename} for files whose library row was recorded at extraction."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT filename, book_id FROM processed_files WHERE book_id IS NOT NULL ORDER BY filename"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    owned = {}
    for row in rows:
        if (BOOKS_DIR / row["filename"]).exists():
            owned.setdefault(row["book_id"], row["filename"])
    return owned


def collect_file_books():
    """List of (title, author, title_lower, author_lower, filename) from books/ folder."""
    file_books = []
    if not BOOKS_DIR.exists():
        return file_books
    for file_path in sorted(BOOKS_DIR.glob("*")):
        if file_path.suffix.lower() in ALLOWED_EXTENSIONS:
            title, author = file_identity(file_path)
            file_books.append(
                (
                    title,
                    author,
                    title.lower().strip(),
                    author.lower().strip(),
                    file_path.name,
                )
            )
    return file_books


def match_starting_books(books_rows):
    """Annotate DB books with is_starting and reference counts."""
    owned_by_id = owned_book_files()
    recorded_files = set(owned_by_id.values())
    # Files not yet tied to a row fall back to title/author matching
    file_books = [fb for fb in collect_file_books() if fb[4] not in recorded_files]
    matched_files = set()
    books_list = []

    conn = get_db_connection()
    cursor = conn.cursor()

    # Books added to the library by title, with no file
    try:
        cursor.execute("SELECT id FROM books WHERE on_shelf = 1")
        shelf_ids = {row["id"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        shelf_ids = set()

    try:
        cursor.execute("SELECT id, year FROM books WHERE year IS NOT NULL")
        years = {row["id"]: row["year"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        years = {}

    # What was folded into each work: its parts and its other names
    includes = {}
    try:
        cursor.execute("SELECT book_id, title FROM work_aliases ORDER BY title")
        for row in cursor.fetchall():
            includes.setdefault(row["book_id"], []).append(row["title"])
    except sqlite3.OperationalError:
        pass  # a database from before works were folded

    for book in books_rows:
        db_title = book["title"].lower().strip()
        db_author = (book["author"] or "").lower().strip()

        is_starting = False
        source_filename = None
        if book["id"] in owned_by_id:
            is_starting = True
            source_filename = owned_by_id[book["id"]]
        elif book["id"] in shelf_ids:
            is_starting = True
        elif "summary of" not in db_title and "paperback" not in db_title:
            matched_file_idx = -1
            for idx, (file_title, file_author, file_title_lower, file_author_lower, filename) in enumerate(
                file_books
            ):
                if idx in matched_files:
                    continue
                exact_title = db_title == file_title_lower
                fuzzy_title = (
                    (db_title in file_title_lower or file_title_lower in db_title)
                    and len(db_title) > 5
                )
                db_author_tokens = {
                    t for t in db_author.replace(".", " ").split() if len(t) > 1
                }
                file_author_tokens = {
                    t for t in file_author_lower.replace(".", " ").split() if len(t) > 1
                }
                author_overlap = bool(db_author_tokens & file_author_tokens)
                author_match = (
                    not db_author
                    or db_author == "unknown"
                    or not file_author_lower
                    or file_author_lower == "unknown"
                    or db_author == file_author_lower
                    or db_author in file_author_lower
                    or file_author_lower in db_author
                    or author_overlap
                )
                if (exact_title or fuzzy_title) and author_match and "summary" not in db_title:
                    is_starting = True
                    matched_file_idx = idx
                    source_filename = filename
                    break
            if is_starting and matched_file_idx >= 0:
                matched_files.add(matched_file_idx)

        # Library books always shown; other nodes must be verified (any kind)
        if not is_starting:
            if not db_author or db_author == "unknown":
                # Allow cocktail/film labels that use kind as author-ish
                kind_val = book["kind"] if "kind" in book.keys() and book["kind"] else "book"
                if kind_val == "book":
                    continue
            if not (bool(book["verified"]) if "verified" in book.keys() else False):
                continue

        kind = "book"
        if "kind" in book.keys() and book["kind"]:
            kind = book["kind"]

        cursor.execute(
            "SELECT COUNT(*) as count FROM book_references WHERE source_book_id = ?",
            (book["id"],),
        )
        outgoing = cursor.fetchone()["count"]
        cursor.execute(
            "SELECT COUNT(*) as count FROM book_references WHERE referenced_book_id = ?",
            (book["id"],),
        )
        incoming = cursor.fetchone()["count"]

        books_list.append(
            {
                "id": book["id"],
                "title": book["title"],
                "author": book["author"],
                "is_starting": is_starting,
                "outgoing_count": outgoing,
                "incoming_count": incoming,
                "verified": bool(book["verified"]) if "verified" in book.keys() else False,
                "match_score": book["match_score"] if "match_score" in book.keys() else None,
                "open_library_id": (
                    book["open_library_id"] if "open_library_id" in book.keys() else None
                ),
                "kind": kind,
                "cover_id": book["cover_id"] if "cover_id" in book.keys() else None,
                "cover_url": book_cover_url(book, size="M"),
                "source_filename": source_filename,
                "no_file": is_starting and not source_filename and book["id"] in shelf_ids,
                "year": years.get(book["id"]),
                "includes": [
                    t for t in includes.get(book["id"], []) if t.lower() != book["title"].lower()
                ],
            }
        )

    conn.close()
    return books_list


def get_process_status_snapshot():
    with _process_lock:
        healed = _reconcile_process_status_locked()
        snap = copy.deepcopy(_process_status)
    if healed:
        _persist_process_status(snap)
    return snap


def update_process_status(updates):
    """Update in-memory status quickly; persist to disk without holding the lock."""
    if not isinstance(updates, dict):
        return
    payload = dict(updates)
    payload["updated_at"] = time.time()
    with _process_lock:
        _process_status.update(payload)
        snap = copy.deepcopy(_process_status)
    _persist_process_status(snap)


def run_processing_job(only_filenames=None, force=False):
    """Background worker for book processing."""
    try:
        from process_books import process_pending_files

        def on_progress(status):
            update_process_status(status)

        update_process_status(
            {
                "status": "running",
                "phase": "starting",
                "message": "Borges: Opening the stack. This may take a few minutes.",
                "agent": "borges",
                "errors": [],
                "books_processed": [],
            }
        )
        result = process_pending_files(
            progress_callback=on_progress,
            quiet=True,
            only_filenames=only_filenames,
            force=force,
        )
        if isinstance(result, dict):
            update_process_status(result)
        else:
            update_process_status(
                {
                    "status": "done",
                    "phase": "idle",
                    "message": "Shelving complete.",
                    "current_file": None,
                }
            )
    except Exception as e:
        update_process_status(
            {
                "status": "error",
                "phase": "idle",
                "current_file": None,
                "message": str(e),
                "errors": [str(e)],
            }
        )
    finally:
        # Never leave a zombie "running" flag after this worker exits
        with _process_lock:
            still_this_worker = _process_thread is threading.current_thread()
            if still_this_worker and _process_status.get("status") == "running":
                _mark_interrupted_locked(
                    "Borges lost his place (worker stopped). Click Ask Borges & Lucien to continue."
                )
                snap = copy.deepcopy(_process_status)
            else:
                snap = None
        if snap is not None:
            _persist_process_status(snap)


@app.route("/")
def index():
    """Serve the main HTML page (explore). Manage library at /manage."""
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    """Browsers ask for this path whatever the page declares."""
    return app.send_static_file("favicon.svg")


@app.route("/manage")
def manage_page():
    """Local-only library management: add/remove books and publish to Pages."""
    return render_template("manage.html")


@app.route("/api/books")
def api_books():
    """Return a JSON list of all books from the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, author, verified, match_score, cover_id, cover_url, open_library_id, kind
            FROM books ORDER BY title
            """
        )
        books = cursor.fetchall()
        conn.close()
        return jsonify(match_starting_books(books))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/references")
def api_references():
    """Return a JSON list of all source-target reference pairs."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT source_book_id, referenced_book_id, manner
            FROM book_references
            ORDER BY source_book_id, referenced_book_id
            """
        )
        references = cursor.fetchall()
        references_list = [
            {
                "source": ref["source_book_id"],
                "target": ref["referenced_book_id"],
                "manner": ref["manner"],
            }
            for ref in references
        ]
        conn.close()
        return jsonify(references_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats")
def api_stats():
    """Return basic statistics about the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM books")
        total_books = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM book_references")
        total_references = cursor.fetchone()["count"]

        existing_titles = set()
        if BOOKS_DIR.exists():
            for file_path in BOOKS_DIR.glob("*"):
                if file_path.suffix.lower() in ALLOWED_EXTENSIONS:
                    title = extract_title_from_filename(file_path.name)
                    existing_titles.add(title.lower().strip())

        if existing_titles:
            placeholders = ",".join(["?" for _ in existing_titles])
            cursor.execute(
                f"SELECT COUNT(*) as count FROM books WHERE LOWER(TRIM(title)) IN ({placeholders})",
                list(existing_titles),
            )
            starting_books = cursor.fetchone()["count"]
        else:
            starting_books = 0

        conn.close()
        return jsonify(
            {
                "total_books": total_books,
                "total_references": total_references,
                "starting_books": starting_books,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search")
def api_search():
    """Search books by title or author."""
    try:
        query = request.args.get("q", "").lower().strip()
        if not query:
            return jsonify([])
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, author
            FROM books
            WHERE LOWER(title) LIKE ? OR LOWER(author) LIKE ?
            ORDER BY title
            LIMIT 50
            """,
            (f"%{query}%", f"%{query}%"),
        )
        books = cursor.fetchall()
        books_list = [
            {"id": book["id"], "title": book["title"], "author": book["author"]}
            for book in books
        ]
        conn.close()
        return jsonify(books_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/book/<int:book_id>/references")
def api_book_references(book_id):
    """Get outgoing and incoming references for a specific book."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, author, verified, match_score, cover_id, cover_url, kind, open_library_id FROM books WHERE id = ?",
            (book_id,),
        )
        source_book = cursor.fetchone()
        if not source_book:
            conn.close()
            return jsonify({"error": "Book not found"}), 404

        cursor.execute(
            """
            SELECT b.id, b.title, b.author, b.verified, b.match_score, b.cover_id, b.cover_url, b.kind,
                   br.context, br.manner
            FROM book_references br
            JOIN books b ON br.referenced_book_id = b.id
            WHERE br.source_book_id = ?
            ORDER BY b.title
            """,
            (book_id,),
        )
        outgoing = [
            {
                "id": r["id"],
                "title": r["title"],
                "author": r["author"],
                "verified": bool(r["verified"]),
                "match_score": r["match_score"],
                "kind": r["kind"] if "kind" in r.keys() and r["kind"] else "book",
                "context": r["context"],
                "manner": r["manner"],
                "cover_url": book_cover_url(r, size="S"),
            }
            for r in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT b.id, b.title, b.author, b.verified, b.match_score, b.cover_id, b.cover_url, b.kind
            FROM book_references br
            JOIN books b ON br.source_book_id = b.id
            WHERE br.referenced_book_id = ?
            ORDER BY b.title
            """,
            (book_id,),
        )
        incoming = [
            {
                "id": r["id"],
                "title": r["title"],
                "author": r["author"],
                "verified": bool(r["verified"]),
                "match_score": r["match_score"],
                "kind": r["kind"] if "kind" in r.keys() and r["kind"] else "book",
                "cover_url": book_cover_url(r, size="S"),
            }
            for r in cursor.fetchall()
        ]

        skipped = []
        try:
            cursor.execute(
                """
                SELECT raw_title, reason FROM skipped_mentions
                WHERE source_book_id = ?
                ORDER BY raw_title
                LIMIT 40
                """,
                (book_id,),
            )
            skipped = [
                {"title": r["raw_title"], "reason": r["reason"]} for r in cursor.fetchall()
            ]
        except sqlite3.OperationalError:
            skipped = []

        conn.close()
        return jsonify(
            {
                "source_book": {
                    "id": source_book["id"],
                    "title": source_book["title"],
                    "author": source_book["author"],
                    "verified": bool(source_book["verified"]),
                    "match_score": source_book["match_score"],
                    "kind": (
                        source_book["kind"]
                        if "kind" in source_book.keys() and source_book["kind"]
                        else "book"
                    ),
                    "cover_id": source_book["cover_id"]
                    if "cover_id" in source_book.keys()
                    else None,
                    "open_library_id": source_book["open_library_id"],
                    "cover_url": book_cover_url(source_book, size="M"),
                },
                "references": outgoing,
                "outgoing": outgoing,
                "incoming": incoming,
                "skipped": skipped,
                "count": len(outgoing),
                "outgoing_count": len(outgoing),
                "incoming_count": len(incoming),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Accept EPUB/TXT uploads into the books/ folder."""
    try:
        if "files" not in request.files and "file" not in request.files:
            return jsonify({"error": "No files provided"}), 400

        files = request.files.getlist("files")
        if not files:
            single = request.files.get("file")
            files = [single] if single else []

        if not files:
            return jsonify({"error": "No files provided. Choose one or more .epub or .txt files."}), 400

        BOOKS_DIR.mkdir(exist_ok=True)
        saved = []
        rejected = []

        # Content hashes of the library, to refuse a second copy of the same file
        from process_books import get_file_hash

        existing_hashes = {
            get_file_hash(path): path.name
            for path in BOOKS_DIR.glob("*")
            if path.suffix.lower() in ALLOWED_EXTENSIONS
        }

        for file in files:
            if not file or not file.filename:
                continue
            original = file.filename
            suffix = Path(original).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                rejected.append(
                    {
                        "filename": original,
                        "reason": "Only .epub and .txt files are allowed",
                    }
                )
                continue

            cleaned = re.sub(r'[<>:"/\\|?*]', "", Path(original).stem).strip()
            cleaned = cleaned or secure_filename(Path(original).stem) or "book"
            dest_name = f"{cleaned}{suffix}"
            dest = BOOKS_DIR / dest_name

            counter = 1
            while dest.exists():
                dest_name = f"{cleaned} ({counter}){suffix}"
                dest = BOOKS_DIR / dest_name
                counter += 1

            try:
                file.save(dest)
            except Exception as save_err:
                rejected.append({"filename": original, "reason": str(save_err)})
                continue
            digest = get_file_hash(dest)
            if digest and digest in existing_hashes:
                dest.unlink(missing_ok=True)
                rejected.append(
                    {
                        "filename": original,
                        "reason": f"Already in your library as “{existing_hashes[digest]}”",
                    }
                )
                continue
            existing_hashes[digest] = dest.name
            saved.append({"filename": dest.name, "path": str(dest)})

        if not saved:
            reason = rejected[0]["reason"] if rejected else "No valid files uploaded"
            return jsonify(
                {
                    "error": reason,
                    "rejected": rejected,
                }
            ), 400

        return jsonify(
            {
                "saved": saved,
                "rejected": rejected,
                "message": f"Uploaded {len(saved)} file(s). Run process to extract references.",
            }
        )
    except Exception as e:
        return jsonify({"error": f"Upload failed: {e}"}), 500


@app.route("/api/process", methods=["POST"])
def api_process():
    """Start background processing of new/changed book files."""
    global _process_thread
    body = request.get_json(silent=True) or {}
    only_filenames = body.get("filenames")
    force = bool(body.get("force"))
    if only_filenames and isinstance(only_filenames, str):
        only_filenames = [only_filenames]

    with _process_lock:
        already = _process_status.get("status") == "running" or (
            _process_thread is not None and _process_thread.is_alive()
        )
        snap = copy.deepcopy(_process_status) if already else None

    if already:
        return (
            jsonify(
                {
                    "error": "Processing is already running. Wait for it to finish, or watch the progress banner.",
                    **snap,
                }
            ),
            409,
        )

    thread = threading.Thread(
        target=run_processing_job,
        kwargs={"only_filenames": only_filenames, "force": force},
        daemon=True,
    )
    with _process_lock:
        _process_thread = thread
        _process_status.update(
            {
                "status": "running",
                "phase": "starting",
                "message": "Borges: In the queue — getting ready to read…",
                "agent": "borges",
                "processed": 0,
                "total": 0,
                "errors": [],
                "books_processed": [],
            }
        )
        _save_process_status()
    thread.start()
    return jsonify(get_process_status_snapshot())


@app.route("/api/book/<int:book_id>/reprocess", methods=["POST"])
def api_reprocess_book(book_id):
    """Force re-extraction for one owned library book."""
    global _process_thread
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, author, verified, match_score, cover_id, kind FROM books WHERE id = ?",
            (book_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Book not found"}), 404

        annotated = match_starting_books([row])
        if not annotated or not annotated[0].get("is_starting"):
            return jsonify({"error": "Only library (owned) books can be reprocessed"}), 400
        filename = annotated[0].get("source_filename")
        if not filename:
            return jsonify({"error": "Could not find the source file for this book"}), 404

        with _process_lock:
            already = _process_status.get("status") == "running" or (
                _process_thread is not None and _process_thread.is_alive()
            )
        if already:
            return jsonify({"error": "Processing already in progress"}), 409

        thread = threading.Thread(
            target=run_processing_job,
            kwargs={"only_filenames": [filename], "force": True},
            daemon=True,
        )
        with _process_lock:
            _process_thread = thread
            _process_status.update(
                {
                    "status": "running",
                    "phase": "starting",
                    "message": f"Borges: Re-reading {filename}",
                    "agent": "borges",
                    "processed": 0,
                    "total": 1,
                    "errors": [],
                    "books_processed": [],
                    "current_file": filename,
                }
            )
            _save_process_status()
        thread.start()
        return jsonify(get_process_status_snapshot())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/process/status")
def api_process_status():
    """Poll processing progress (also auto-heals dead workers)."""
    snap = get_process_status_snapshot()
    snap["worker_alive"] = _worker_alive()
    return jsonify(snap)


@app.route("/api/process/reset", methods=["POST"])
def api_process_reset():
    """Clear a stuck running flag when no worker is alive."""
    with _process_lock:
        if _worker_alive():
            return (
                jsonify(
                    {
                        "error": "Processing is still actively running.",
                        **copy.deepcopy(_process_status),
                    }
                ),
                409,
            )
        _process_status.update(
            {
                "status": "idle",
                "phase": "idle",
                "current_file": None,
                "message": "Ready",
                "errors": [],
                "updated_at": time.time(),
            }
        )
        snap = copy.deepcopy(_process_status)
    _persist_process_status(snap)
    return jsonify(snap)


@app.route("/api/covers/backfill", methods=["POST"])
def api_covers_backfill():
    """Fetch Open Library covers for books that are missing them."""
    try:
        from process_books import backfill_missing_covers

        body = request.get_json(silent=True) or {}
        limit = int(body.get("limit") or 80)
        limit = max(1, min(limit, 200))
        result = backfill_missing_covers(limit=limit, quiet=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def api_health():
    """Basic dependency / path health check."""
    info = {
        "ok": True,
        "books_dir": BOOKS_DIR.exists() and BOOKS_DIR.is_dir(),
        "books_writable": False,
        "spacy": False,
        "spacy_model": False,
        "database": Path("database.db").exists(),
        "judge": {"available": False, "model": None, "reason": None},
    }
    try:
        from judge import judge_status
        from reader import reader_enabled, reader_model

        info["judge"] = judge_status()
        info["reader"] = {
            "enabled": reader_enabled(),
            "model": reader_model() if reader_enabled() else None,
        }
        from auditor import auditor_enabled

        info["auditor"] = {"enabled": auditor_enabled() and info["judge"]["available"]}
    except Exception as e:
        info["judge"]["reason"] = str(e)
    try:
        BOOKS_DIR.mkdir(exist_ok=True)
        probe = BOOKS_DIR / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        info["books_writable"] = True
    except Exception as e:
        info["ok"] = False
        info["books_error"] = str(e)
    try:
        import spacy

        info["spacy"] = True
        try:
            from process_books import get_nlp, spacy_model_name

            get_nlp()
            info["spacy_model"] = True
            info["spacy_model_name"] = spacy_model_name()
        except (OSError, RuntimeError):
            info["ok"] = False
    except ImportError:
        info["ok"] = False
    return jsonify(info)


@app.route("/api/network/export")
def api_network_export():
    """Export the full network as portable JSON (same shape as docs/)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, author, verified, match_score, cover_id, cover_url, open_library_id, kind
            FROM books ORDER BY title
            """
        )
        books = match_starting_books(cursor.fetchall())
        cursor.execute(
            """
            SELECT source_book_id, referenced_book_id, context
            FROM book_references
            ORDER BY source_book_id, referenced_book_id
            """
        )
        references = [
            {
                "source": ref["source_book_id"],
                "target": ref["referenced_book_id"],
                "context": ref["context"],
            }
            for ref in cursor.fetchall()
        ]
        conn.close()
        # Drop local-only fields for portable export
        portable_books = []
        for book in books:
            portable_books.append(
                {
                    "id": book["id"],
                    "title": book["title"],
                    "author": book["author"],
                    "is_starting": book["is_starting"],
                    "verified": book["verified"],
                    "match_score": book["match_score"],
                    "open_library_id": book.get("open_library_id"),
                    "cover_id": book.get("cover_id"),
                    "cover_url": book.get("cover_url"),
                    "kind": book.get("kind") or "book",
                    "outgoing_count": book.get("outgoing_count"),
                    "incoming_count": book.get("incoming_count"),
                }
            )
        return jsonify({"books": portable_books, "references": references})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/network/import", methods=["POST"])
def api_network_import():
    """
    Merge an exported network into the local database.
    Accepts {books: [...], references: [...]} or a raw books array with
    separate references key.
    """
    try:
        from process_books import init_database, normalize_title, deduplicate_books

        payload = request.get_json(silent=True) or {}
        books_in = payload.get("books")
        refs_in = payload.get("references")
        if books_in is None and isinstance(payload, list):
            return jsonify({"error": "Send an object with books and references arrays"}), 400
        if not isinstance(books_in, list) or not isinstance(refs_in, list):
            return jsonify({"error": "Expected JSON with books and references arrays"}), 400

        init_database(quiet=True)
        conn = get_db_connection()
        cursor = conn.cursor()
        id_map = {}
        imported_books = 0
        updated_books = 0

        for book in books_in:
            title = (book.get("title") or "").strip()
            if not title:
                continue
            author = (book.get("author") or "Unknown").strip() or "Unknown"
            ol_id = (book.get("open_library_id") or "").strip()
            if not ol_id:
                ol_id = f"manual_{normalize_title(title) or title.lower()}"
            verified = 1 if book.get("verified") else 0
            match_score = book.get("match_score")
            cover_id = book.get("cover_id")
            old_id = book.get("id")

            cursor.execute(
                "SELECT id FROM books WHERE open_library_id = ?",
                (ol_id,),
            )
            existing = cursor.fetchone()
            if not existing:
                cursor.execute(
                    "SELECT id FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))",
                    (title,),
                )
                existing = cursor.fetchone()

            if existing:
                new_id = existing["id"]
                cursor.execute(
                    """
                    UPDATE books
                    SET title = ?, author = ?,
                        verified = MAX(verified, ?),
                        match_score = COALESCE(?, match_score),
                        cover_id = COALESCE(?, cover_id),
                        open_library_id = CASE
                            WHEN open_library_id LIKE 'manual_%' THEN ?
                            ELSE open_library_id
                        END
                    WHERE id = ?
                    """,
                    (
                        title,
                        author,
                        verified,
                        match_score,
                        cover_id,
                        ol_id,
                        new_id,
                    ),
                )
                updated_books += 1
            else:
                try:
                    cursor.execute(
                        """
                        INSERT INTO books
                            (title, author, open_library_id, verified, match_score, cover_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (title, author, ol_id, verified, match_score, cover_id),
                    )
                    new_id = cursor.lastrowid
                    imported_books += 1
                except sqlite3.IntegrityError:
                    cursor.execute(
                        "SELECT id FROM books WHERE open_library_id = ?",
                        (ol_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue
                    new_id = row["id"]
                    updated_books += 1

            if old_id is not None:
                id_map[int(old_id)] = int(new_id)

        imported_refs = 0
        for ref in refs_in:
            src = ref.get("source")
            tgt = ref.get("target")
            if src is None or tgt is None:
                continue
            src = id_map.get(int(src))
            tgt = id_map.get(int(tgt))
            if not src or not tgt or src == tgt:
                continue
            context = ref.get("context")
            cursor.execute(
                """
                SELECT 1 FROM book_references
                WHERE source_book_id = ? AND referenced_book_id = ?
                """,
                (src, tgt),
            )
            if cursor.fetchone():
                if context:
                    cursor.execute(
                        """
                        UPDATE book_references
                        SET context = COALESCE(context, ?)
                        WHERE source_book_id = ? AND referenced_book_id = ?
                        """,
                        (context, src, tgt),
                    )
                continue
            cursor.execute(
                """
                INSERT INTO book_references (source_book_id, referenced_book_id, context)
                VALUES (?, ?, ?)
                """,
                (src, tgt, context),
            )
            imported_refs += 1

        conn.commit()
        merged = deduplicate_books(cursor, conn, quiet=True)
        conn.close()
        return jsonify(
            {
                "imported_books": imported_books,
                "updated_books": updated_books,
                "imported_references": imported_refs,
                "merged_duplicates": merged,
                "message": (
                    f"Imported {imported_books} books and {imported_refs} references"
                    + (f" (updated {updated_books})" if updated_books else "")
                ),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# —— Review queue: fast accept / reject, remembered across re-extraction ——


@app.route("/review")
def review_page():
    return render_template("review.html")


def _set_override(cursor, source_id, raw_key, action, target_id=None):
    """A decision made by a person on /review. It outranks the Auditor's."""
    from process_books import _save_override

    _save_override(cursor, source_id, raw_key, action, target_id, made_by="you")


@app.route("/api/review/queue")
def api_review_queue():
    """Links awaiting a decision and mentions that were dropped, per library book."""
    show_all = request.args.get("show") == "all"
    source_filter = request.args.get("source", type=int)
    conn = get_db_connection()
    cursor = conn.cursor()

    where, params = [], []
    if source_filter:
        where.append("br.source_book_id = ?")
        params.append(source_filter)
    if not show_all:
        # What still needs a person: not confirmed, and not passed by the Auditor
        where.append("IFNULL(br.confirmed, 0) = 0")
        where.append("IFNULL(br.audit, '') NOT IN ('ok', 'fixed')")
    cursor.execute(
        f"""
        SELECT br.audit, br.audit_reason,
               br.source_book_id, s.title AS source_title, s.author AS source_author,
               t.id AS target_id, t.title, t.author, t.kind, t.match_score,
               t.cover_id, t.cover_url, t.open_library_id,
               br.context, br.raw_title, br.manner, IFNULL(br.confirmed, 0) AS confirmed
        FROM book_references br
        JOIN books s ON s.id = br.source_book_id
        JOIN books t ON t.id = br.referenced_book_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY s.title, t.title
        """,
        params,
    )
    links = [
        {
            "type": "link",
            "source_id": r["source_book_id"],
            "source_title": r["source_title"],
            "source_author": r["source_author"],
            "target_id": r["target_id"],
            "title": r["title"],
            "author": r["author"],
            "kind": r["kind"] or "book",
            "match_score": r["match_score"],
            "cover_url": book_cover_url(r, size="M"),
            "open_library_id": r["open_library_id"],
            "context": r["context"],
            "raw_title": r["raw_title"],
            "manner": r["manner"],
            "audit": r["audit"],
            "audit_reason": r["audit_reason"],
            "confirmed": bool(r["confirmed"]),
        }
        for r in cursor.fetchall()
    ]

    where, params = [], []
    if source_filter:
        where.append("sm.source_book_id = ?")
        params.append(source_filter)
    if not show_all:
        where.append("IFNULL(sm.reason, '') != 'Rejected by you'")
    cursor.execute(
        f"""
        SELECT sm.id, sm.source_book_id, s.title AS source_title, sm.raw_title, sm.reason,
               sm.context, sm.suggested_title, sm.suggested_author
        FROM skipped_mentions sm
        JOIN books s ON s.id = sm.source_book_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY (sm.suggested_title IS NULL), s.title, sm.raw_title
        """,
        params,
    )
    dropped = [
        {
            "type": "dropped",
            "skipped_id": r["id"],
            "source_id": r["source_book_id"],
            "source_title": r["source_title"],
            "raw_title": r["raw_title"],
            "reason": r["reason"],
            "context": r["context"],
            "suggested_title": r["suggested_title"],
            "suggested_author": r["suggested_author"],
        }
        for r in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT b.id, b.title FROM books b
        WHERE b.id IN (SELECT source_book_id FROM book_references
                       UNION SELECT source_book_id FROM skipped_mentions)
        ORDER BY b.title
        """
    )
    sources = [{"id": r["id"], "title": r["title"]} for r in cursor.fetchall()]
    cursor.execute(
        """
        SELECT IFNULL(audit, 'unchecked') AS state, COUNT(*) AS n
        FROM book_references GROUP BY 1
        """
    )
    audit = {r["state"]: r["n"] for r in cursor.fetchall()}
    conn.close()
    return jsonify({"links": links, "dropped": dropped, "sources": sources, "audit": audit})


@app.route("/api/review/link", methods=["POST"])
def api_review_link():
    """Confirm, reject or restore one source -> target link."""
    from process_books import override_key, clean_context

    data = request.get_json(silent=True) or {}
    action = data.get("action")
    try:
        source_id = int(data.get("source_id"))
        target_id = int(data.get("target_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "source_id and target_id are required"}), 400
    if action not in ("confirm", "reject", "restore") or source_id == target_id:
        return jsonify({"error": "Unknown action"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT context, raw_title FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?",
        (source_id, target_id),
    )
    row = cursor.fetchone()
    raw_title = (row["raw_title"] if row else None) or data.get("raw_title")
    context = (row["context"] if row else None) or data.get("context")
    raw_key = override_key(raw_title) if raw_title else ""

    if action == "confirm":
        if not row:
            conn.close()
            return jsonify({"error": "Link not found"}), 404
        cursor.execute(
            "UPDATE book_references SET confirmed = 1 WHERE source_book_id = ? AND referenced_book_id = ?",
            (source_id, target_id),
        )
        _set_override(cursor, source_id, raw_key, "link", target_id)
    elif action == "reject":
        cursor.execute(
            "DELETE FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?",
            (source_id, target_id),
        )
        _set_override(cursor, source_id, raw_key, "reject")
        _set_override(cursor, source_id, f"#{target_id}", "reject")
    else:  # restore (undo a reject)
        cursor.execute(
            "DELETE FROM reference_overrides WHERE source_book_id = ? AND raw_key IN (?, ?)",
            (source_id, raw_key, f"#{target_id}"),
        )
        if not row:
            cursor.execute(
                """
                INSERT INTO book_references
                    (source_book_id, referenced_book_id, context, raw_title, confirmed)
                VALUES (?, ?, ?, ?, 0)
                """,
                (source_id, target_id, clean_context(context), raw_title),
            )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "action": action})


@app.route("/api/review/lookup")
def api_review_lookup():
    """Find the catalogue record a dropped mention should point at."""
    from process_books import lookup_open_library

    title = (request.args.get("title") or "").strip()
    author = (request.args.get("author") or "").strip() or None
    if not title:
        return jsonify({"error": "title is required"}), 400
    match = lookup_open_library(title, author=author)
    if match and match.get("cover_id"):
        match["cover_url"] = f"https://covers.openlibrary.org/b/id/{match['cover_id']}-M.jpg"
    return jsonify({"match": match})


@app.route("/api/review/dropped", methods=["POST"])
def api_review_dropped():
    """Link a dropped mention to a work, or dismiss it for good."""
    from process_books import (
        get_or_create_book,
        override_key,
        ensure_cover_for_book,
        _link_reference,
    )

    data = request.get_json(silent=True) or {}
    action = data.get("action")
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_book_id, raw_title, context FROM skipped_mentions WHERE id = ?",
        (data.get("skipped_id"),),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Mention not found"}), 404
    source_id, raw_title, context = row
    raw_key = override_key(raw_title)

    if action == "dismiss":
        _set_override(cursor, source_id, raw_key, "reject")
        cursor.execute(
            "UPDATE skipped_mentions SET reason = 'Rejected by you' WHERE id = ?",
            (data.get("skipped_id"),),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    if action != "link":
        conn.close()
        return jsonify({"error": "Unknown action"}), 400

    title = (data.get("title") or raw_title or "").strip()
    author = (data.get("author") or "").strip() or None
    target_id = get_or_create_book(
        title, cursor, conn, require_verified=True, kind="book", author=author
    )
    if target_id is None or target_id == source_id:
        conn.close()
        return jsonify({"error": "No catalogue match for that title and author"}), 404
    try:
        ensure_cover_for_book(target_id, cursor, conn)
    except Exception:
        pass
    _link_reference(cursor, source_id, target_id, context, raw_title, confirmed=True)
    _set_override(cursor, source_id, raw_key, "link", target_id)
    cursor.execute("DELETE FROM skipped_mentions WHERE id = ?", (data.get("skipped_id"),))
    conn.commit()
    cursor.execute("SELECT title, author FROM books WHERE id = ?", (target_id,))
    linked = cursor.fetchone()
    conn.close()
    return jsonify({"ok": True, "target_id": target_id, "title": linked[0], "author": linked[1]})


# —— Shared catalog admin ——


def _is_local_request() -> bool:
    """True when the request comes from this machine (loopback)."""
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _admin_authorized() -> bool:
    """
    With ADMIN_TOKEN set, that token is required from everyone.
    Without it, requests from this machine are admin and nobody else is.
    """
    if not ADMIN_TOKEN:
        return _is_local_request()
    header = request.headers.get("X-Admin-Token", "")
    body_token = ""
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            body_token = payload.get("token") or ""
    form_token = request.form.get("token", "") if request.form else ""
    session_token = session.get("admin_token", "")
    provided = header or body_token or form_token or session_token
    return bool(provided) and hmac.compare_digest(str(provided), ADMIN_TOKEN)


@app.before_request
def guard_writes():
    """
    Every request that changes something must come from this app's own pages
    (not another website open in the browser) and, when it comes from another
    machine, must carry the admin token.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return jsonify({"error": "Cross-site request refused"}), 403
    if request.path == "/api/admin/login":
        return None
    if not _is_local_request() and not _admin_authorized():
        return jsonify({"error": "Unauthorized. Changes from another machine need ADMIN_TOKEN."}), 401
    return None


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _admin_authorized():
            return jsonify({"error": "Unauthorized. Set ADMIN_TOKEN or log in at /admin."}), 401
        return fn(*args, **kwargs)

    return wrapper


@app.route("/api/library/books/<int:book_id>", methods=["DELETE"])
@require_admin
def api_library_delete_book(book_id):
    """
    Remove an owned library book: delete source file, clear extraction edges,
    and drop the book row if nothing else still cites it.
    """
    try:
        from process_books import clear_processed_file

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, author, verified, match_score, cover_id, cover_url,
                   open_library_id, kind
            FROM books WHERE id = ?
            """,
            (book_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Book not found"}), 404

        annotated = match_starting_books([row])
        if not annotated or not annotated[0].get("is_starting"):
            conn.close()
            return jsonify({"error": "Only owned library books can be removed"}), 400

        filename = annotated[0].get("source_filename")
        deleted_file = False
        if filename:
            path = BOOKS_DIR / filename
            if path.exists():
                path.unlink()
                deleted_file = True
            clear_processed_file(filename)

        try:
            cursor.execute("UPDATE books SET on_shelf = 0 WHERE id = ?", (book_id,))
        except sqlite3.OperationalError:
            pass  # a database from before books could be added by title
        cursor.execute(
            "DELETE FROM book_references WHERE source_book_id = ?", (book_id,)
        )
        cursor.execute(
            "DELETE FROM skipped_mentions WHERE source_book_id = ?", (book_id,)
        )
        cursor.execute(
            "SELECT COUNT(*) AS c FROM book_references WHERE referenced_book_id = ?",
            (book_id,),
        )
        incoming = cursor.fetchone()["c"]
        removed_row = False
        if incoming == 0:
            cursor.execute("DELETE FROM books WHERE id = ?", (book_id,))
            removed_row = True
        conn.commit()
        conn.close()
        return jsonify(
            {
                "ok": True,
                "deleted_file": deleted_file,
                "filename": filename,
                "removed_book_row": removed_row,
                "kept_as_citation": not removed_row,
                "message": (
                    f'Removed "{annotated[0]["title"]}" from your library.'
                    + (
                        " It stays on the graph as a cited work."
                        if not removed_row
                        else ""
                    )
                ),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/library/books", methods=["POST"])
@require_admin
def api_library_add_by_title():
    """Add a book to the library by its title, with no file to read."""
    from process_books import add_shelf_book, init_database

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    author = (data.get("author") or "").strip()
    if not title:
        return jsonify({"error": "A title is required"}), 400
    if len(title) > 300 or len(author) > 200:
        return jsonify({"error": "Title or author is too long"}), 400
    try:
        init_database(quiet=True)
        book = add_shelf_book(title, author)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    mentioned = book["mentioned_by"]
    message = f'Added "{book["title"]}" by {book["author"]}.'
    if mentioned:
        message += " Mentioned by " + ", ".join(mentioned) + "."
    elif not book["found_in_catalogue"]:
        message += " No catalogue had it, so it is filed as you typed it."
    return jsonify({"ok": True, "book": book, "message": message})


@app.route("/api/library/books/<int:book_id>", methods=["PATCH"])
@require_admin
def api_library_edit_book(book_id):
    """Set a library book's title and author by hand, then re-file it in the catalogue."""
    from process_books import refresh_owned_record

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    author = (data.get("author") or "").strip()
    if not title:
        return jsonify({"error": "A title is required"}), 400
    if len(title) > 300 or len(author) > 200:
        return jsonify({"error": "Title or author is too long"}), 400

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM books WHERE id = ?", (book_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "Book not found"}), 404
    if book_id not in owned_book_files():
        conn.close()
        return jsonify({"error": "Only library books can be edited"}), 400

    cursor.execute(
        "UPDATE books SET title = ?, author = ?, user_edited = 1 WHERE id = ?",
        (title, author or "Unknown", book_id),
    )
    conn.commit()
    refiled = False
    try:
        refiled = refresh_owned_record(book_id, title, author, cursor, conn)
    except Exception:
        pass  # offline: the edit itself is saved
    conn.close()
    return jsonify({"ok": True, "title": title, "author": author or "Unknown", "refiled": refiled})


@app.route("/api/admin/export-static", methods=["POST"])
@require_admin
def api_admin_export_static():
    """Rebuild docs/ for GitHub Pages from the current database."""
    try:
        from export_static import main as export_main

        export_main()
        return jsonify(
            {
                "ok": True,
                "message": "Exported docs/ for GitHub Pages. Commit and push docs/ to publish.",
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin")
def admin_page():
    """Simple catalog curation dashboard."""
    return render_template("admin.html")


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not ADMIN_TOKEN:
        if _is_local_request():
            return jsonify({"ok": True})
        return jsonify({"error": "Set ADMIN_TOKEN on the server to manage it remotely"}), 401
    if not hmac.compare_digest(token, ADMIN_TOKEN):
        return jsonify({"error": "Invalid token"}), 401
    session["admin_token"] = token
    return jsonify({"ok": True})


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("admin_token", None)
    return jsonify({"ok": True})


@app.route("/api/admin/session")
def api_admin_session():
    return jsonify({"authenticated": _admin_authorized()})


@app.route("/api/admin/catalog/stats")
@require_admin
def api_admin_catalog_stats():
    from catalog import catalog_stats, get_conn, init_catalog

    init_catalog(quiet=True)
    conn = get_conn()
    stats = catalog_stats(conn)
    conn.close()
    return jsonify(stats)


@app.route("/api/admin/catalog/works")
@require_admin
def api_admin_catalog_works():
    from catalog import get_conn, init_catalog, list_works

    init_catalog(quiet=True)
    q = request.args.get("q", "")
    conn = get_conn()
    works = list_works(conn, query=q)
    conn.close()
    return jsonify(works)


@app.route("/api/admin/catalog/works", methods=["POST"])
@require_admin
def api_admin_catalog_create_work():
    from catalog import get_conn, init_catalog, upsert_work, get_work

    init_catalog(quiet=True)
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    try:
        conn = get_conn()
        work_id, created = upsert_work(
            conn,
            title=title,
            author=data.get("author"),
            open_library_id=data.get("open_library_id"),
            cover_id=data.get("cover_id"),
            notes=data.get("notes"),
        )
        work = get_work(conn, work_id)
        conn.close()
        return jsonify({"work": work, "created": created})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/catalog/works/<int:work_id>")
@require_admin
def api_admin_catalog_work_detail(work_id):
    from catalog import get_conn, get_work, list_mentions_for_work

    conn = get_conn()
    work = get_work(conn, work_id)
    if not work:
        conn.close()
        return jsonify({"error": "Work not found"}), 404
    mentions = list_mentions_for_work(conn, work_id)
    conn.close()
    return jsonify({"work": work, **mentions})


@app.route("/api/admin/catalog/works/<int:work_id>", methods=["PATCH"])
@require_admin
def api_admin_catalog_update_work(work_id):
    from catalog import get_conn, update_work, get_work

    data = request.get_json(silent=True) or {}
    conn = get_conn()
    ok = update_work(
        conn,
        work_id,
        title=data.get("title"),
        author=data.get("author"),
        open_library_id=data.get("open_library_id"),
        cover_id=data.get("cover_id"),
        notes=data.get("notes"),
    )
    if not ok:
        conn.close()
        return jsonify({"error": "Work not found"}), 404
    work = get_work(conn, work_id)
    conn.close()
    return jsonify({"work": work})


@app.route("/api/admin/catalog/works/<int:work_id>", methods=["DELETE"])
@require_admin
def api_admin_catalog_delete_work(work_id):
    from catalog import get_conn, delete_work

    conn = get_conn()
    ok = delete_work(conn, work_id)
    conn.close()
    if not ok:
        return jsonify({"error": "Work not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/catalog/mentions", methods=["POST"])
@require_admin
def api_admin_catalog_add_mention():
    from catalog import get_conn, add_mention, upsert_work

    data = request.get_json(silent=True) or {}
    source_id = data.get("source_work_id")
    if not source_id:
        return jsonify({"error": "source_work_id is required"}), 400

    conn = get_conn()
    try:
        target_id = data.get("target_work_id")
        if not target_id:
            title = (data.get("target_title") or "").strip()
            if not title:
                conn.close()
                return jsonify({"error": "Provide target_work_id or target_title"}), 400
            target_id, _ = upsert_work(
                conn,
                title=title,
                author=data.get("target_author"),
                open_library_id=data.get("target_open_library_id"),
                cover_id=data.get("target_cover_id"),
            )
        mention_id, created = add_mention(
            conn,
            int(source_id),
            int(target_id),
            context=data.get("context"),
            confidence=float(data.get("confidence") or 1.0),
        )
        conn.close()
        return jsonify(
            {
                "mention_id": mention_id,
                "target_work_id": int(target_id),
                "created": created,
            }
        )
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/catalog/mentions/<int:mention_id>", methods=["DELETE"])
@require_admin
def api_admin_catalog_delete_mention(mention_id):
    from catalog import get_conn, delete_mention

    conn = get_conn()
    ok = delete_mention(conn, mention_id)
    conn.close()
    if not ok:
        return jsonify({"error": "Mention not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/catalog/lookup")
@require_admin
def api_admin_catalog_lookup():
    """Open Library lookup helper for the admin form."""
    title = (request.args.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title query required"}), 400
    try:
        from process_books import lookup_open_library

        match = lookup_open_library(title)
        if not match:
            return jsonify({"match": None})
        return jsonify(
            {
                "match": {
                    "title": match.get("title"),
                    "author": match.get("author"),
                    "open_library_id": match.get("open_library_id"),
                    "cover_id": match.get("cover_id"),
                    "match_score": match.get("match_score"),
                    "cover_url": (
                        f"https://covers.openlibrary.org/b/id/{match['cover_id']}-M.jpg"
                        if match.get("cover_id")
                        else None
                    ),
                }
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/catalog/import-local", methods=["POST"])
@require_admin
def api_admin_catalog_import_local():
    """Bootstrap catalog from existing personal library extractions."""
    from catalog import get_conn, init_catalog, import_from_local_library

    init_catalog(quiet=True)
    conn = get_conn()
    result = import_from_local_library(conn)
    conn.close()
    return jsonify(result)


@app.route("/api/catalog/works/<int:work_id>/mentions")
def api_public_catalog_mentions(work_id):
    """Public read API — future shelf lookup uses this."""
    from catalog import get_conn, get_work, list_mentions_for_work, init_catalog

    init_catalog(quiet=True)
    conn = get_conn()
    work = get_work(conn, work_id)
    if not work:
        conn.close()
        return jsonify({"error": "Work not found"}), 404
    mentions = list_mentions_for_work(conn, work_id)
    conn.close()
    return jsonify({"work": work, **mentions})


@app.route("/api/catalog/search")
def api_public_catalog_search():
    from catalog import get_conn, init_catalog, list_works

    init_catalog(quiet=True)
    q = request.args.get("q", "")
    conn = get_conn()
    works = list_works(conn, query=q, limit=40)
    conn.close()
    return jsonify(works)


def ensure_runtime_dirs():
    Path("templates").mkdir(exist_ok=True)
    Path("static").mkdir(exist_ok=True)
    BOOKS_DIR.mkdir(exist_ok=True)
    try:
        from process_books import init_database

        init_database(quiet=True)
    except Exception:
        pass
    try:
        from catalog import init_catalog

        init_catalog(quiet=True)
    except Exception:
        pass


ensure_runtime_dirs()

if __name__ == "__main__":
    print("Books in Books - Web Server")
    print("=" * 50)
    # Local by default. To reach it from another machine set HOST=0.0.0.0 and
    # ADMIN_TOKEN. The debugger lets a visitor run code, so it is never enabled
    # on a non-local address.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    local_only = host in ("127.0.0.1", "localhost", "::1")
    debug = os.environ.get("FLASK_DEBUG") == "1" and local_only
    print(f"Server starting on http://{'localhost' if local_only else host}:{port}")
    if not local_only and not ADMIN_TOKEN:
        print("Note: reachable from other machines, read-only for them (ADMIN_TOKEN not set).")
    print("Press Ctrl+C to stop")
    print()
    if not debug:
        try:
            from waitress import serve
        except ImportError:
            serve = None
        if serve is not None:
            # A real WSGI server instead of Flask's development one
            serve(app, host=host, port=port, threads=8)
            raise SystemExit(0)
    app.run(debug=debug, host=host, port=port, threaded=True, use_reloader=False)
