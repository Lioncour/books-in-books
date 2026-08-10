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
import os
import time
from pathlib import Path
from functools import wraps
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024  # 80 MB
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "books-in-books-dev-secret")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "books-admin")

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


def collect_file_books():
    """List of (title, author, title_lower, author_lower, filename) from books/ folder."""
    file_books = []
    if not BOOKS_DIR.exists():
        return file_books
    for file_path in sorted(BOOKS_DIR.glob("*")):
        if file_path.suffix.lower() in ALLOWED_EXTENSIONS:
            title, author = parse_filename_parts(file_path.stem)
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
    file_books = collect_file_books()
    matched_files = set()
    books_list = []

    conn = get_db_connection()
    cursor = conn.cursor()

    for book in books_rows:
        db_title = book["title"].lower().strip()
        db_author = (book["author"] or "").lower().strip()

        is_starting = False
        source_filename = None
        if "summary of" not in db_title and "paperback" not in db_title:
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
    """Serve the main HTML page."""
    return render_template("index.html")


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
            SELECT source_book_id, referenced_book_id
            FROM book_references
            ORDER BY source_book_id, referenced_book_id
            """
        )
        references = cursor.fetchall()
        references_list = [
            {"source": ref["source_book_id"], "target": ref["referenced_book_id"]}
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
            "SELECT id, title, author, verified, match_score, cover_id, cover_url, kind FROM books WHERE id = ?",
            (book_id,),
        )
        source_book = cursor.fetchone()
        if not source_book:
            conn.close()
            return jsonify({"error": "Book not found"}), 404

        cursor.execute(
            """
            SELECT b.id, b.title, b.author, b.verified, b.match_score, b.cover_id, b.cover_url, b.kind, br.context
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
    }
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
            spacy.load("en_core_web_trf")
            info["spacy_model"] = True
        except OSError:
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


@app.route("/verify")
def verify_page():
    """Serve the verification page."""
    return render_template("verify.html")


@app.route("/api/verify")
def api_verify():
    """Run verification and return results."""
    try:
        import spacy
        from collections import defaultdict

        try:
            from ebooklib import epub
            import ebooklib
        except ImportError:
            return jsonify({"error": "ebooklib not installed"}), 500

        try:
            nlp = spacy.load("en_core_web_trf")
        except OSError:
            return jsonify(
                {
                    "error": "spaCy model not found. Run: python -m spacy download en_core_web_trf"
                }
            ), 500

        conn = get_db_connection()
        cursor = conn.cursor()

        if not BOOKS_DIR.exists():
            return jsonify({"error": "Books directory not found"}), 404

        files = list(BOOKS_DIR.glob("*.txt")) + list(BOOKS_DIR.glob("*.epub"))
        if not files:
            return jsonify({"error": "No book files found"}), 404

        results = []

        for file_path in files:
            title, author = parse_filename_parts(file_path.stem)
            cursor.execute(
                "SELECT id FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))",
                (title,),
            )
            result = cursor.fetchone()
            if not result:
                continue

            source_book_id = result[0]
            cursor.execute(
                """
                SELECT b.id, b.title, b.author
                FROM book_references br
                JOIN books b ON br.referenced_book_id = b.id
                WHERE br.source_book_id = ?
                ORDER BY b.title
                """,
                (source_book_id,),
            )
            db_references = cursor.fetchall()
            db_references_list = [
                {"id": r["id"], "title": r["title"], "author": r["author"]}
                for r in db_references
            ]
            db_titles = {r["title"].lower().strip() for r in db_references}

            try:
                if file_path.suffix.lower() == ".epub":
                    book = epub.read_epub(str(file_path))
                    text = ""
                    for item in book.get_items():
                        if item.get_type() == ebooklib.ITEM_DOCUMENT:
                            text += item.get_content().decode("utf-8", errors="ignore") + "\n"
                    text = re.sub("<[^<]+?>", "", text)
                else:
                    with open(file_path, "r", encoding="utf-8") as f:
                        text = f.read()

                doc = nlp(text)
                references_found = []
                for ent in doc.ents:
                    if ent.label_ == "WORK_OF_ART":
                        referenced_title = ent.text.strip()
                        if referenced_title and len(referenced_title) > 2:
                            start = max(0, ent.start_char - 50)
                            end = min(len(text), ent.end_char + 50)
                            context = text[start:end].replace("\n", " ").strip()
                            references_found.append(
                                {"title": referenced_title, "context": context}
                            )

                refs_by_title = defaultdict(list)
                for ref in references_found:
                    refs_by_title[ref["title"]].append(ref["context"])

                found_references_list = []
                for ref_title, contexts in sorted(refs_by_title.items()):
                    found_references_list.append(
                        {
                            "title": ref_title,
                            "count": len(contexts),
                            "contexts": contexts[:3],
                        }
                    )

                found_titles = {t.lower().strip() for t in refs_by_title.keys()}
                matches = db_titles & found_titles
                only_in_db = list(db_titles - found_titles)
                only_in_text = [
                    t for t in refs_by_title.keys() if t.lower().strip() not in db_titles
                ]

                results.append(
                    {
                        "title": title,
                        "author": author,
                        "db_references_count": len(db_references),
                        "found_references_count": len(found_references_list),
                        "matches": len(matches),
                        "db_references": db_references_list,
                        "found_references": found_references_list,
                        "matched_titles": list(matches),
                        "only_in_database": only_in_db[:10],
                        "only_in_text": only_in_text[:10],
                    }
                )
            except Exception as e:
                results.append({"title": title, "author": author, "error": str(e)})

        conn.close()
        return jsonify({"books": results, "total_books": len(results)})
    except Exception as e:
        import traceback

        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# —— Shared catalog admin ——


def _admin_authorized() -> bool:
    header = request.headers.get("X-Admin-Token", "")
    query = request.args.get("token", "")
    body_token = ""
    if request.is_json and isinstance(request.json, dict):
        body_token = request.json.get("token") or ""
    form_token = request.form.get("token", "") if request.form else ""
    session_token = session.get("admin_token", "")
    provided = header or query or body_token or form_token or session_token
    return bool(provided) and provided == ADMIN_TOKEN


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _admin_authorized():
            return jsonify({"error": "Unauthorized. Set ADMIN_TOKEN or log in at /admin."}), 401
        return fn(*args, **kwargs)

    return wrapper


@app.route("/admin")
def admin_page():
    """Simple catalog curation dashboard."""
    return render_template("admin.html", default_hint="books-admin")


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if token != ADMIN_TOKEN:
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
    print("Server starting on http://localhost:5000")
    print("Press Ctrl+C to stop")
    print()
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
