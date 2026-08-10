#!/usr/bin/env python3
"""
Literary Links AI Visualizer - Data Processing Script
Processes book text files to extract references and populate the SQLite database.
Importable by the Flask app for web-driven processing.
"""

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

try:
    from ebooklib import epub
    import ebooklib
except ImportError:
    epub = None
    ebooklib = None

nlp = None

# Duo that powers extraction: Borges reads the text; Lucien verifies & files.
BORGES = "Borges"
LUCIEN = "Lucien"


def borges_says(text: str) -> str:
    return f"{BORGES}: {text}"


def lucien_says(text: str) -> str:
    return f"{LUCIEN}: {text}"


def get_nlp():
    """Lazy-load spaCy model so importing this module does not require it immediately."""
    global nlp
    if nlp is not None:
        return nlp
    try:
        import spacy
        nlp = spacy.load("en_core_web_trf")
        return nlp
    except OSError as e:
        raise RuntimeError(
            "spaCy model not found. Run: python -m spacy download en_core_web_trf"
        ) from e
    except ImportError as e:
        raise RuntimeError("spaCy is not installed. Run: pip install spacy") from e


TITLE_STOPWORDS = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for"}
MIN_VERIFY_SCORE = 0.72
MIN_QUERY_CHARS = 4
MEDIA_TITLE_BLOCKLIST = {
    "star trek",
    "star wars",
    "stargate",
    "battlestar galactica",
    "short circuit",
    "aliens",
    "alien",
    "blade runner",
    "the matrix",
    "jurassic park",
    "indiana jones",
    "back to the future",
    "the godfather",
    "pulp fiction",
    "fight club",
    "terminator",
    "the terminator",
    "predator",
    "mad max",
    "ghostbusters",
}
NLP_CHUNK_CHARS = 90000


def init_database(quiet: bool = False):
    """Initialize the SQLite database with required tables."""
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            open_library_id TEXT NOT NULL UNIQUE,
            verified INTEGER DEFAULT 0,
            match_score REAL
        )
        """
    )

    # Migrate older databases
    cursor.execute("PRAGMA table_info(books)")
    columns = {row[1] for row in cursor.fetchall()}
    if "verified" not in columns:
        cursor.execute("ALTER TABLE books ADD COLUMN verified INTEGER DEFAULT 0")
    if "match_score" not in columns:
        cursor.execute("ALTER TABLE books ADD COLUMN match_score REAL")
    if "cover_id" not in columns:
        cursor.execute("ALTER TABLE books ADD COLUMN cover_id TEXT")
    if "cover_url" not in columns:
        cursor.execute("ALTER TABLE books ADD COLUMN cover_url TEXT")
    if "kind" not in columns:
        cursor.execute("ALTER TABLE books ADD COLUMN kind TEXT DEFAULT 'book'")
        cursor.execute("UPDATE books SET kind = 'book' WHERE kind IS NULL OR kind = ''")

    # Do not auto-retag franchise titles as movies — novels exist (e.g. Star Trek).
    # Kind is set at extraction time from context + domain lookup.

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS book_references (
            source_book_id INTEGER,
            referenced_book_id INTEGER,
            context TEXT,
            FOREIGN KEY (source_book_id) REFERENCES books (id),
            FOREIGN KEY (referenced_book_id) REFERENCES books (id),
            CHECK (source_book_id != referenced_book_id)
        )
        """
    )
    cursor.execute("PRAGMA table_info(book_references)")
    ref_cols = {row[1] for row in cursor.fetchall()}
    if "context" not in ref_cols:
        cursor.execute("ALTER TABLE book_references ADD COLUMN context TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skipped_mentions (
            id INTEGER PRIMARY KEY,
            source_book_id INTEGER NOT NULL,
            raw_title TEXT NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_book_id) REFERENCES books (id)
        )
        """
    )

    cursor.execute(
        """
        DELETE FROM book_references
        WHERE source_book_id = referenced_book_id
        """
    )
    if cursor.rowcount > 0 and not quiet:
        print(f"Removed {cursor.rowcount} existing self-references from database")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_files (
            filename TEXT PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            file_hash TEXT
        )
        """
    )

    merged = deduplicate_books(cursor, conn, quiet=quiet)
    if merged and not quiet:
        print(f"Merged {merged} duplicate book row(s)")

    conn.commit()
    conn.close()
    if not quiet:
        print("Database initialized successfully!")


def _book_merge_score(row) -> tuple:
    """Prefer verified, higher match_score, real Open Library ids, richer metadata."""
    ol_id = row["open_library_id"] or ""
    is_manual = ol_id.startswith("manual_") or ol_id.startswith("gbooks_")
    return (
        int(bool(row["verified"])),
        float(row["match_score"] or 0),
        0 if is_manual else 1,
        1 if row["cover_id"] else 0,
        1 if row["author"] and row["author"] != "Unknown" else 0,
        -int(row["id"]),
    )


def _remap_book_id(cursor, old_id: int, new_id: int):
    if old_id == new_id:
        return
    cursor.execute(
        """
        UPDATE book_references
        SET source_book_id = ?
        WHERE source_book_id = ?
        """,
        (new_id, old_id),
    )
    cursor.execute(
        """
        UPDATE book_references
        SET referenced_book_id = ?
        WHERE referenced_book_id = ?
        """,
        (new_id, old_id),
    )
    cursor.execute(
        "DELETE FROM book_references WHERE source_book_id = referenced_book_id"
    )
    cursor.execute(
        """
        UPDATE skipped_mentions
        SET source_book_id = ?
        WHERE source_book_id = ?
        """,
        (new_id, old_id),
    )
    cursor.execute("DELETE FROM books WHERE id = ?", (old_id,))


def deduplicate_books(cursor, conn, quiet: bool = True) -> int:
    """
    Merge duplicate book rows: same open_library_id, or same normalized title
    when authors are compatible. Rewires references to the surviving id.
    """
    cursor.execute(
        """
        SELECT id, title, author, open_library_id, verified, match_score, cover_id
        FROM books
        """
    )
    rows = [dict(zip(
        ["id", "title", "author", "open_library_id", "verified", "match_score", "cover_id"],
        r,
    )) for r in cursor.fetchall()]

    merged = 0

    # 1) Exact open_library_id collisions (should be rare with UNIQUE)
    by_ol = {}
    for row in rows:
        key = (row["open_library_id"] or "").strip()
        if not key:
            continue
        by_ol.setdefault(key, []).append(row)

    for group in by_ol.values():
        if len(group) < 2:
            continue
        group.sort(key=_book_merge_score, reverse=True)
        keeper = group[0]
        for loser in group[1:]:
            _remap_book_id(cursor, loser["id"], keeper["id"])
            merged += 1
            if not quiet:
                print(f"  Merged OL duplicate '{loser['title']}' → id {keeper['id']}")

    # Refresh after OL merges
    cursor.execute(
        """
        SELECT id, title, author, open_library_id, verified, match_score, cover_id
        FROM books
        """
    )
    rows = [dict(zip(
        ["id", "title", "author", "open_library_id", "verified", "match_score", "cover_id"],
        r,
    )) for r in cursor.fetchall()]

    # 2) Same normalized title + compatible author
    by_title = {}
    for row in rows:
        key = normalize_title(row["title"] or "")
        if not key:
            continue
        by_title.setdefault(key, []).append(row)

    for group in by_title.values():
        if len(group) < 2:
            continue
        # Split into author-compatible clusters
        clusters = []
        for row in group:
            placed = False
            author = (row["author"] or "").lower().strip()
            for cluster in clusters:
                ca = (cluster[0]["author"] or "").lower().strip()
                compatible = (
                    not author
                    or not ca
                    or author == "unknown"
                    or ca == "unknown"
                    or author == ca
                    or author in ca
                    or ca in author
                )
                if compatible:
                    cluster.append(row)
                    placed = True
                    break
            if not placed:
                clusters.append([row])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            # Prefer keeping a real Open Library id over manual placeholders
            cluster.sort(key=_book_merge_score, reverse=True)
            keeper = cluster[0]
            for loser in cluster[1:]:
                # Don't merge two strongly verified different OL works
                k_ol = keeper["open_library_id"] or ""
                l_ol = loser["open_library_id"] or ""
                both_real = (
                    k_ol
                    and l_ol
                    and not k_ol.startswith("manual_")
                    and not l_ol.startswith("manual_")
                    and not k_ol.startswith("gbooks_")
                    and not l_ol.startswith("gbooks_")
                    and k_ol != l_ol
                )
                if both_real and keeper["verified"] and loser["verified"]:
                    continue
                # Copy better metadata onto keeper
                if (not keeper.get("cover_id")) and loser.get("cover_id"):
                    cursor.execute(
                        "UPDATE books SET cover_id = ? WHERE id = ?",
                        (loser["cover_id"], keeper["id"]),
                    )
                if (
                    (not keeper.get("author") or keeper["author"] == "Unknown")
                    and loser.get("author")
                    and loser["author"] != "Unknown"
                ):
                    cursor.execute(
                        "UPDATE books SET author = ? WHERE id = ?",
                        (loser["author"], keeper["id"]),
                    )
                if (
                    (keeper["open_library_id"] or "").startswith("manual_")
                    and loser.get("open_library_id")
                    and not (loser["open_library_id"] or "").startswith("manual_")
                ):
                    try:
                        cursor.execute(
                            """
                            UPDATE books
                            SET open_library_id = ?, verified = ?, match_score = COALESCE(?, match_score)
                            WHERE id = ?
                            """,
                            (
                                loser["open_library_id"],
                                int(bool(loser["verified"])),
                                loser["match_score"],
                                keeper["id"],
                            ),
                        )
                    except sqlite3.IntegrityError:
                        pass
                _remap_book_id(cursor, loser["id"], keeper["id"])
                merged += 1
                if not quiet:
                    print(f"  Merged title duplicate '{loser['title']}' → id {keeper['id']}")

    # Deduplicate reference pairs after remaps
    cursor.execute(
        """
        DELETE FROM book_references
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM book_references
            GROUP BY source_book_id, referenced_book_id
        )
        """
    )

    if merged:
        conn.commit()
    return merged


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, drop light stopwords for comparison."""
    text = title.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    words = [w for w in text.split() if w and w not in TITLE_STOPWORDS]
    return " ".join(words)


def title_similarity(a: str, b: str) -> float:
    """Score how closely two titles match (0–1)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    sa, sb = set(na.split()), set(nb.split())
    if not sa or not sb:
        return 0.0
    jaccard = len(sa & sb) / len(sa | sb)

    if na in nb or nb in na:
        len_ratio = min(len(na), len(nb)) / max(len(na), len(nb))
        jaccard = max(jaccard, 0.65 + 0.35 * len_ratio)

    return jaccard


def token_coverage(query: str, candidate: str) -> float:
    """Fraction of query tokens present in the candidate title."""
    q = set(normalize_title(query).split())
    c = set(normalize_title(candidate).split())
    if not q:
        return 0.0
    return len(q & c) / len(q)


def is_plausible_title_query(title: str) -> bool:
    """Reject tiny / junk spaCy hits before calling Open Library."""
    normalized = normalize_title(title)
    if len(normalized) < MIN_QUERY_CHARS:
        return False
    words = normalized.split()
    if len(words) == 1 and len(normalized) < 6:
        return False
    # Common false positives from WORK_OF_ART / too-generic singles
    blocked = {
        "god",
        "song",
        "poem",
        "movie",
        "film",
        "painting",
        "untitled",
        "scripture",
        "scriptures",
        "commentaries",
        "commentary",
        "testament",
    }
    if title.lower().strip() in blocked or normalized in blocked:
        return False
    # Trailing fragments like "WE ARE A."
    if title.strip().endswith(".") and len(words) <= 3:
        return False
    # Media franchises are handled as movie-kind mentions, not rejected here
    return True


def skip_reason_for_title(title: str) -> Optional[str]:
    """Return a human reason if the title should be skipped before book OL lookup."""
    normalized = normalize_title(title)
    if not is_plausible_title_query(title):
        if len(normalized) < MIN_QUERY_CHARS:
            return "Title too short to verify"
        return "Not a plausible book title"
    return None


def lookup_google_books(title: str) -> Optional[Dict]:
    """Secondary verification via Google Books when Open Library is weak."""
    try:
        response = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f'intitle:"{title}"', "maxResults": 5},
            timeout=20,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
    except Exception:
        return None

    best = None
    best_score = 0.0
    for item in items:
        info = item.get("volumeInfo") or {}
        candidate_title = info.get("title") or ""
        if not candidate_title:
            continue
        score = title_similarity(title, candidate_title)
        coverage = token_coverage(title, candidate_title)
        if coverage < 0.8 or score < 0.8:
            continue
        authors = info.get("authors") or ["Unknown"]
        if score > best_score:
            best_score = score
            best = {
                "title": candidate_title,
                "author": authors[0],
                "open_library_id": f"gbooks_{item.get('id', normalize_title(candidate_title))}",
                "match_score": round(score, 3),
                "cover_id": None,
            }
            image = (info.get("imageLinks") or {}).get("thumbnail")
            if image:
                best["cover_url"] = image
    if best and best["match_score"] >= 0.85:
        return best
    return None


def lookup_open_library(title: str) -> Optional[Dict]:
    """
    Search Open Library and return the best verified match, or None.
    Result keys: title, author, open_library_id, match_score
    """
    if not is_plausible_title_query(title):
        return None

    try:
        response = requests.get(
            "https://openlibrary.org/search.json",
            params={"title": title, "limit": 8},
            timeout=30,
        )
        response.raise_for_status()
        docs = response.json().get("docs") or []

        def pick_best(candidates):
            query_words = normalize_title(title).split()
            short_query = len(query_words) <= 2
            local_best = None
            local_score = 0.0
            for doc in candidates:
                candidate_title = doc.get("title") or ""
                if not candidate_title:
                    continue
                score = title_similarity(title, candidate_title)
                coverage = token_coverage(title, candidate_title)
                if coverage < (1.0 if short_query else 0.75):
                    continue
                if short_query and score < 0.92:
                    continue
                has_author = bool(doc.get("author_name"))
                if has_author:
                    score = min(1.0, score + 0.02)
                if score > local_score:
                    local_score = score
                    author_name = (
                        doc.get("author_name", ["Unknown"])[0]
                        if doc.get("author_name")
                        else "Unknown"
                    )
                    cover_id = None
                    cover_ids = doc.get("cover_i")
                    if cover_ids is not None:
                        cover_id = str(cover_ids)
                    local_best = {
                        "title": candidate_title,
                        "author": author_name,
                        "open_library_id": doc.get("key")
                        or f"ol_{normalize_title(candidate_title)}",
                        "match_score": round(score, 3),
                        "cover_id": cover_id,
                    }
            return local_best

        best = pick_best(docs)
        if not best:
            response = requests.get(
                "https://openlibrary.org/search.json",
                params={"q": title, "limit": 8},
                timeout=30,
            )
            response.raise_for_status()
            best = pick_best(response.json().get("docs") or [])
    except Exception as e:
        print(f"Open Library lookup failed for '{title}': {e}")
        return None

    if not best:
        return None
    # Franchise titles (Star Trek, etc.) can be real novels — allow OL matches.
    # Film/TV routing is handled by classify_kind via context, not by blocking books.
    if best["match_score"] >= MIN_VERIFY_SCORE and best["author"] != "Unknown":
        return best
    if best["match_score"] >= 0.95:
        return best
    # Borderline: try Google Books as a second opinion
    if best["match_score"] >= 0.6:
        gb = lookup_google_books(title)
        if gb:
            return gb
    return None


def _find_local_book(title: str, cursor, verified_only: bool = False) -> Optional[int]:
    """Find an existing book by exact or strong fuzzy title match."""
    cursor.execute(
        "SELECT id, verified FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))",
        (title,),
    )
    result = cursor.fetchone()
    if result:
        book_id, verified = result
        if (not verified_only) or verified:
            return book_id

    cursor.execute("SELECT id, title, verified FROM books")
    all_books = cursor.fetchall()
    best_id = None
    best_score = 0.0
    for book_id, existing_title, verified in all_books:
        if verified_only and not verified:
            continue
        score = title_similarity(title, existing_title)
        if score > best_score:
            best_score = score
            best_id = book_id
    if best_id is not None and best_score >= MIN_VERIFY_SCORE:
        return best_id
    return None


def _upsert_verified_match(cursor, conn, match: Dict, kind: str = "book") -> Optional[int]:
    """Insert or update a verified work from a domain lookup match dict."""
    kind = kind or match.get("kind") or "book"
    cover_url = match.get("cover_url")
    cursor.execute(
        "SELECT id FROM books WHERE open_library_id = ?",
        (match["open_library_id"],),
    )
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            """
            UPDATE books
            SET title = ?, author = ?, verified = 1, match_score = ?,
                cover_id = COALESCE(?, cover_id),
                cover_url = COALESCE(?, cover_url),
                kind = ?
            WHERE id = ?
            """,
            (
                match["title"],
                match["author"],
                match["match_score"],
                match.get("cover_id"),
                cover_url,
                kind,
                existing[0],
            ),
        )
        conn.commit()
        return existing[0]

    cursor.execute(
        "SELECT id FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?)) AND IFNULL(kind, 'book') = ?",
        (match["title"], kind),
    )
    same_title = cursor.fetchone()
    if same_title:
        cursor.execute(
            """
            UPDATE books
            SET author = ?, open_library_id = ?, verified = 1, match_score = ?,
                cover_id = COALESCE(?, cover_id),
                cover_url = COALESCE(?, cover_url),
                kind = ?
            WHERE id = ?
            """,
            (
                match["author"],
                match["open_library_id"],
                match["match_score"],
                match.get("cover_id"),
                cover_url,
                kind,
                same_title[0],
            ),
        )
        conn.commit()
        return same_title[0]

    try:
        cursor.execute(
            """
            INSERT INTO books (title, author, open_library_id, verified, match_score, cover_id, cover_url, kind)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                match["title"],
                match["author"],
                match["open_library_id"],
                match["match_score"],
                match.get("cover_id"),
                cover_url,
                kind,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        cursor.execute(
            "SELECT id FROM books WHERE open_library_id = ?",
            (match["open_library_id"],),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def get_or_create_book(
    title: str,
    cursor,
    conn,
    require_verified: bool = False,
    kind: str = "book",
    context: Optional[str] = None,
) -> Optional[int]:
    """
    Get or create a work via local cache then domain lookup.

    kind: book (Open Library), movie, music, cocktail.
    Owned library books should call with require_verified=False, kind='book'.
    """
    kind = (kind or "book").lower()
    if kind not in ("book", "movie", "music", "cocktail"):
        kind = "book"

    # Prefer local match of the same kind
    cursor.execute(
        """
        SELECT id, title, verified, kind FROM books
        WHERE IFNULL(kind, 'book') = ?
        """,
        (kind,),
    )
    best_id = None
    best_score = 0.0
    for book_id, existing_title, verified, _k in cursor.fetchall():
        if require_verified and not verified:
            continue
        score = title_similarity(title, existing_title)
        if score > best_score:
            best_score = score
            best_id = book_id
    if best_id is not None and best_score >= MIN_VERIFY_SCORE:
        return best_id

    if kind != "book":
        from mention_kinds import lookup_by_kind

        match = lookup_by_kind(kind, title)
        if match:
            return _upsert_verified_match(cursor, conn, match, kind=kind)
        if require_verified:
            return None
        manual_id = f"manual_{kind}_{normalize_title(title) or title.strip().lower()}"
        cursor.execute("SELECT id FROM books WHERE open_library_id = ?", (manual_id,))
        existing = cursor.fetchone()
        if existing:
            return existing[0]
        cursor.execute(
            """
            INSERT INTO books (title, author, open_library_id, verified, match_score, kind)
            VALUES (?, ?, ?, 0, NULL, ?)
            """,
            (title, kind.title(), manual_id, kind),
        )
        conn.commit()
        return cursor.lastrowid

    # —— Books (Open Library path) ——
    local_id = _find_local_book(title, cursor, verified_only=require_verified)
    if local_id is not None:
        return local_id

    if not require_verified:
        local_any = _find_local_book(title, cursor, verified_only=False)
        if local_any is not None:
            return local_any

    match = lookup_open_library(title)
    if match:
        match["kind"] = "book"
        return _upsert_verified_match(cursor, conn, match, kind="book")

    if require_verified:
        return None

    manual_id = f"manual_{normalize_title(title) or title.strip().lower()}"
    cursor.execute("SELECT id FROM books WHERE open_library_id = ?", (manual_id,))
    existing = cursor.fetchone()
    if existing:
        return existing[0]

    cursor.execute(
        """
        INSERT INTO books (title, author, open_library_id, verified, match_score, kind)
        VALUES (?, ?, ?, 0, NULL, 'book')
        """,
        (title, "Unknown", manual_id),
    )
    conn.commit()
    return cursor.lastrowid


def _store_references(
    references_found,
    source_book_id,
    cursor,
    conn,
    quiet=False,
    progress_callback=None,
):
    """references_found: iterable of str titles OR dicts with title/context."""
    from mention_kinds import classify_kind

    added = 0
    skipped = 0
    cursor.execute("DELETE FROM skipped_mentions WHERE source_book_id = ?", (source_book_id,))

    items = []
    for item in references_found:
        if isinstance(item, dict):
            items.append(item)
        else:
            items.append({"title": item, "context": None})

    total = len(items)
    for idx, item in enumerate(items, 1):
        referenced_title = (item.get("title") or "").strip()
        context = item.get("context")
        if not referenced_title:
            continue
        try:
            kind = item.get("kind_hint") or classify_kind(referenced_title, context)
            if kind not in ("book", "movie", "music", "cocktail"):
                kind = classify_kind(referenced_title, context)
            agent_verb = {
                "book": "Verifying",
                "movie": "Checking film",
                "music": "Checking track",
                "cocktail": "Checking drink",
            }.get(kind, "Verifying")

            if progress_callback and idx % 3 == 0:
                progress_callback(
                    {
                        "phase": "open_library",
                        "message": lucien_says(
                            f'{agent_verb} "{referenced_title}" ({idx}/{total})'
                        ),
                        "agent": "lucien",
                    }
                )

            if kind == "book":
                reason = skip_reason_for_title(referenced_title)
                if reason:
                    skipped += 1
                    cursor.execute(
                        "INSERT INTO skipped_mentions (source_book_id, raw_title, reason) VALUES (?, ?, ?)",
                        (source_book_id, referenced_title, reason),
                    )
                    if not quiet:
                        print(f"  Skipped ({reason}): {referenced_title}")
                    continue

            referenced_book_id = get_or_create_book(
                referenced_title,
                cursor,
                conn,
                require_verified=True,
                kind=kind,
                context=context,
            )
            if referenced_book_id is None:
                skipped += 1
                reason = {
                    "book": "No strong Open Library / Google Books match",
                    "movie": "No strong film match",
                    "music": "No strong music match",
                    "cocktail": "Not in cocktail lexicon",
                }.get(kind, "No strong match")
                cursor.execute(
                    "INSERT INTO skipped_mentions (source_book_id, raw_title, reason) VALUES (?, ?, ?)",
                    (source_book_id, referenced_title, reason),
                )
                if not quiet:
                    print(f"  Skipped unverified ({kind}): {referenced_title}")
                continue
            if source_book_id == referenced_book_id:
                continue
            try:
                ensure_cover_for_book(referenced_book_id, cursor, conn)
            except Exception:
                pass
            cursor.execute(
                "SELECT 1 FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?",
                (source_book_id, referenced_book_id),
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO book_references (source_book_id, referenced_book_id, context) VALUES (?, ?, ?)",
                    (source_book_id, referenced_book_id, context),
                )
                added += 1
                if not quiet:
                    print(f"  Added verified {kind}: {referenced_title}")
            elif context:
                cursor.execute(
                    "UPDATE book_references SET context = COALESCE(context, ?) WHERE source_book_id = ? AND referenced_book_id = ?",
                    (context, source_book_id, referenced_book_id),
                )
        except Exception as e:
            if not quiet:
                print(f"  Error processing reference '{referenced_title}': {e}")
    conn.commit()
    if not quiet:
        print(
            f"  Found {len(items)} candidates; added {added} verified, skipped {skipped} unverified"
        )


def extract_cocktail_mentions(text: str) -> List[Dict]:
    """
    Find named cocktails via lexicon + drink context (NLP often misses these).
    """
    from mention_kinds import (
        AMBIGUOUS_COCKTAILS,
        COCKTAIL_CONTEXT_CUES,
        COCKTAIL_NAMES,
    )

    if not text:
        return []

    # Normalize ampersands so "rum & coke" matches "rum and coke"
    search_text = re.sub(r"\s*&\s*", " and ", text)

    mentions = {}
    # Longest names first so "gin and tonic" wins over shorter fragments
    for key in sorted(COCKTAIL_NAMES.keys(), key=len, reverse=True):
        # Allow simple plurals (mojitos, martinis)
        pattern = re.compile(
            rf"(?<![A-Za-z]){re.escape(key)}s?(?![A-Za-z])",
            re.I,
        )
        for match in pattern.finditer(search_text):
            ctx_start = max(0, match.start() - 90)
            ctx_end = min(len(search_text), match.end() + 90)
            context = search_text[ctx_start:ctx_end].replace("\n", " ").strip()
            if key in AMBIGUOUS_COCKTAILS and not COCKTAIL_CONTEXT_CUES.search(context):
                continue
            if key == "manhattan" and re.search(
                r"\b(street|avenue|nursing|home|park|city|nyc|new york)\b", context, re.I
            ):
                if not COCKTAIL_CONTEXT_CUES.search(context):
                    continue
            display = COCKTAIL_NAMES[key]
            k = display.lower()
            if k not in mentions:
                mentions[k] = {"title": display, "context": context, "kind_hint": "cocktail"}
    return list(mentions.values())


def merge_mention_lists(*lists) -> List[Dict]:
    """Dedupe mention dicts by normalized title; keep first context."""
    merged = {}
    for items in lists:
        for item in items or []:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            key = title.lower()
            if key not in merged:
                merged[key] = item
    return list(merged.values())


def extract_work_of_art_mentions(text: str, progress_callback=None):
    nlp_model = get_nlp()
    mentions = {}
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + NLP_CHUNK_CHARS)
        if end < len(text):
            split_at = text.rfind(" ", start + NLP_CHUNK_CHARS // 2, end)
            if split_at > start:
                end = split_at
        chunks.append((start, text[start:end]))
        start = end

    for i, (offset, chunk) in enumerate(chunks, 1):
        if progress_callback:
            progress_callback(
                {
                    "phase": "nlp",
                    "message": borges_says(
                        f"Scanning pages {i}/{len(chunks)}…"
                    ),
                    "agent": "borges",
                }
            )
        doc = nlp_model(chunk)
        for ent in doc.ents:
            if ent.label_ != "WORK_OF_ART":
                continue
            title = ent.text.strip()
            if not title or len(title) <= 2:
                continue
            abs_start = offset + ent.start_char
            abs_end = offset + ent.end_char
            ctx_start = max(0, abs_start - 60)
            ctx_end = min(len(text), abs_end + 60)
            context = text[ctx_start:ctx_end].replace("\n", " ").strip()
            key = title.lower()
            if key not in mentions:
                mentions[key] = {"title": title, "context": context}
    return list(mentions.values())


def process_text_file(
    file_path: Path,
    source_book_id: int,
    cursor,
    conn,
    quiet=False,
    progress_callback=None,
):
    if not quiet:
        print(f"Processing: {file_path.name}")
    try:
        if progress_callback:
            progress_callback(
                {
                    "phase": "reading",
                    "message": borges_says(f"Opening {file_path.name}"),
                    "agent": "borges",
                }
            )
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        if progress_callback:
            progress_callback(
                {
                    "phase": "loading_model",
                    "message": borges_says("Waking up his reading mind…"),
                    "agent": "borges",
                }
            )
        get_nlp()
        mentions = merge_mention_lists(
            extract_work_of_art_mentions(text, progress_callback=progress_callback),
            extract_cocktail_mentions(text),
        )
        if progress_callback:
            progress_callback(
                {
                    "phase": "open_library",
                    "message": lucien_says(
                        f"Checking {len(mentions)} title(s) against the shelves…"
                    ),
                    "agent": "lucien",
                }
            )
        _store_references(
            mentions,
            source_book_id,
            cursor,
            conn,
            quiet=quiet,
            progress_callback=progress_callback,
        )
    except Exception as e:
        if not quiet:
            print(f"Error processing file {file_path}: {e}")
        raise


def process_epub_file(
    file_path: Path,
    source_book_id: int,
    cursor,
    conn,
    quiet=False,
    progress_callback=None,
):
    if epub is None or ebooklib is None:
        raise RuntimeError("ebooklib is not installed. Run: pip install ebooklib")
    if not quiet:
        print(f"Processing EPUB: {file_path.name}")
    try:
        if progress_callback:
            progress_callback(
                {
                    "phase": "reading",
                    "message": borges_says(f"Opening {file_path.name}"),
                    "agent": "borges",
                }
            )
        book = epub.read_epub(str(file_path))
        text = ""
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                text += item.get_content().decode("utf-8", errors="ignore") + "\n"
        text = re.sub("<[^<]+?>", "", text)
        if progress_callback:
            progress_callback(
                {
                    "phase": "loading_model",
                    "message": borges_says("Waking up his reading mind…"),
                    "agent": "borges",
                }
            )
        get_nlp()
        mentions = merge_mention_lists(
            extract_work_of_art_mentions(text, progress_callback=progress_callback),
            extract_cocktail_mentions(text),
        )
        if progress_callback:
            progress_callback(
                {
                    "phase": "open_library",
                    "message": lucien_says(
                        f"Checking {len(mentions)} title(s) against the shelves…"
                    ),
                    "agent": "lucien",
                }
            )
        _store_references(
            mentions,
            source_book_id,
            cursor,
            conn,
            quiet=quiet,
            progress_callback=progress_callback,
        )
    except Exception as e:
        if not quiet:
            print(f"Error processing EPUB file {file_path}: {e}")
        raise


def clean_filename_phrase(text: str) -> str:
    """Turn underscore/slug phrases into a readable title/author string."""
    text = text.replace("_", " ").strip()
    text = re.sub(r"\([^)]*epub[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\((?:pdf|mobi|azw3?|kindle|retail)\)", "", text, flags=re.I)
    text = re.sub(r"\s*\(\d+\)\s*$", "", text)
    text = re.sub(r"\bbobiverse\b", "", text, flags=re.I)
    text = re.sub(r"\bbook\s*\d+\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    return text


def looks_like_person_name(text: str) -> bool:
    """Heuristic: 'First Last' style tokens, not a multi-word book title."""
    cleaned = clean_filename_phrase(text)
    words = [w for w in re.sub(r"[.]", " ", cleaned).split() if w]
    if not (2 <= len(words) <= 4):
        return False
    stop = {
        "the",
        "of",
        "a",
        "an",
        "and",
        "in",
        "to",
        "for",
        "on",
        "with",
        "from",
        "day",
        "road",
        "child",
        "things",
        "like",
        "these",
        "legion",
        "boy",
        "mole",
        "fox",
        "horse",
    }
    if any(w.lower() in stop for w in words):
        return False
    return True


def extract_title_and_author(filename: str) -> tuple:
    """Extract title and author from filename."""
    stem = Path(filename).stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        if len(parts) == 2:
            part1 = clean_filename_phrase(parts[0])
            part2 = clean_filename_phrase(parts[1])
            p1_person = looks_like_person_name(part1)
            p2_person = looks_like_person_name(part2)
            # Author - Title (most of this library)
            if p1_person and not p2_person:
                return part2, part1
            # Title - Author
            if p2_person and not p1_person:
                return part1, part2
            if p1_person and p2_person:
                return part2, part1

            part1_words = len(part1.split())
            part2_words = len(part2.split())
            if part1_words <= 2 and part2_words >= 3:
                return part2, part1
            if part2_words <= 2 and part1_words >= 3:
                return part1, part2
            return part2, part1

    # Common download slug: title_words__author_words
    if "__" in stem:
        left, right = stem.split("__", 1)
        title = clean_filename_phrase(left)
        author = clean_filename_phrase(right)
        return title or stem, author or "Unknown"

    if "_" in stem:
        return clean_filename_phrase(stem) or stem, "Unknown"

    return clean_filename_phrase(stem) or stem, "Unknown"


def get_file_hash(file_path: Path) -> str:
    """Calculate MD5 hash of file for change detection."""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception:
        return ""


def is_file_processed(filename: str, file_hash: str, cursor) -> bool:
    """Check if file has already been processed with the same hash."""
    cursor.execute("SELECT file_hash FROM processed_files WHERE filename = ?", (filename,))
    result = cursor.fetchone()
    if result:
        return result[0] == file_hash
    return False


def mark_file_processed(filename: str, file_hash: str, cursor, conn):
    """Mark a file as processed."""
    cursor.execute(
        """
        INSERT OR REPLACE INTO processed_files (filename, file_hash, processed_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (filename, file_hash),
    )
    conn.commit()


def list_book_files(books_dir: Optional[Path] = None) -> List[Path]:
    """Return all processable book files."""
    books_dir = books_dir or Path("books")
    books_dir.mkdir(exist_ok=True)
    return sorted(list(books_dir.glob("*.txt")) + list(books_dir.glob("*.epub")))


def clear_processed_file(filename: str):
    """Forget that a file was processed so it can be re-run."""
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM processed_files WHERE filename = ?", (filename,))
    conn.commit()
    conn.close()


def process_single_file(
    file_path: Path,
    cursor,
    conn,
    quiet: bool = False,
    progress_callback=None,
    force: bool = False,
) -> Optional[str]:
    """
    Process one book file. Returns the book title if processed, None if skipped.
    Raises on failure.
    """
    file_hash = get_file_hash(file_path)
    if not force and is_file_processed(file_path.name, file_hash, cursor):
        return None

    if force:
        cursor.execute("DELETE FROM processed_files WHERE filename = ?", (file_path.name,))
        conn.commit()

    title, author = extract_title_and_author(file_path.name)
    if progress_callback:
        progress_callback(
            {
                "phase": "starting",
                "message": borges_says(f"Preparing {file_path.name}"),
                "current_file": file_path.name,
                "agent": "borges",
            }
        )
    source_book_id = get_or_create_book(title, cursor, conn, require_verified=False)
    if source_book_id is None:
        raise RuntimeError(f"Could not create library book for '{title}'")
    if author != "Unknown":
        cursor.execute(
            "UPDATE books SET author = ? WHERE id = ? AND (author IS NULL OR author = 'Unknown')",
            (author, source_book_id),
        )
        conn.commit()

    # Drop old outgoing refs before reprocess so results stay fresh
    if force:
        cursor.execute("DELETE FROM book_references WHERE source_book_id = ?", (source_book_id,))
        conn.commit()

    def file_progress(updates):
        if progress_callback:
            payload = {"current_file": file_path.name}
            payload.update(updates)
            progress_callback(payload)

    suffix = file_path.suffix.lower()
    if suffix == ".txt":
        process_text_file(
            file_path,
            source_book_id,
            cursor,
            conn,
            quiet=quiet,
            progress_callback=file_progress,
        )
    elif suffix == ".epub":
        process_epub_file(
            file_path,
            source_book_id,
            cursor,
            conn,
            quiet=quiet,
            progress_callback=file_progress,
        )
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    if progress_callback:
        progress_callback(
            {
                "phase": "saving",
                "message": lucien_says(f"Filing {file_path.name} on the shelves"),
                "current_file": file_path.name,
                "agent": "lucien",
            }
        )
    mark_file_processed(file_path.name, file_hash, cursor, conn)
    # Fill cover art for owned books even when the row already existed
    ensure_cover_for_book(source_book_id, cursor, conn)
    return title


def clean_title_for_lookup(title: str) -> str:
    """Strip filename junk so cover/title lookups work better."""
    t = (title or "").strip()
    t = re.sub(r"\s*\(epub\)\s*", " ", t, flags=re.I)
    t = re.sub(r"\s*\(\d+\)\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def lookup_wikipedia_thumb(title: str) -> Optional[str]:
    """Wikipedia page thumbnail — useful for films, songs, and famous works."""
    from urllib.parse import quote

    query = clean_title_for_lookup(title)
    if not query or len(query) < 2:
        return None
    headers = {
        "User-Agent": "BooksInBooks/1.0 (https://github.com/Lioncour/books-in-books; literary catalog)",
        "Accept": "application/json",
    }
    try:
        response = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(query)}",
            headers=headers,
            timeout=15,
        )
        if not response.ok:
            return None
        data = response.json()
        thumb = (data.get("thumbnail") or {}).get("source")
        if thumb:
            return thumb
        # Some pages only expose originalimage
        return (data.get("originalimage") or {}).get("source")
    except Exception:
        return None


def ensure_cover_for_book(book_id: int, cursor, conn) -> bool:
    """
    If a work has no cover, try Open Library / domain lookup / Wikipedia.
    Returns True when a cover was written.
    """
    cursor.execute("PRAGMA table_info(books)")
    cols = {row[1] for row in cursor.fetchall()}
    has_cover_url = "cover_url" in cols

    select_cols = "id, title, author, cover_id, open_library_id, IFNULL(kind, 'book')"
    if has_cover_url:
        select_cols += ", cover_url"
    cursor.execute(f"SELECT {select_cols} FROM books WHERE id = ?", (book_id,))
    row = cursor.fetchone()
    if not row:
        return False

    if has_cover_url:
        _id, title, author, cover_id, ol_id, kind, cover_url = row
    else:
        _id, title, author, cover_id, ol_id, kind = row
        cover_url = None

    if cover_id or cover_url:
        return False

    lookup_title = clean_title_for_lookup(title)
    new_cover_id = None
    new_cover_url = None
    new_ol = None
    new_author = None
    new_title = None

    if kind == "book":
        match = lookup_open_library(lookup_title)
        if match:
            new_cover_id = match.get("cover_id")
            new_ol = match.get("open_library_id")
            new_author = match.get("author")
            new_title = match.get("title")
        if not new_cover_id and ol_id and str(ol_id).startswith("/works/"):
            try:
                response = requests.get(f"https://openlibrary.org{ol_id}.json", timeout=20)
                if response.ok:
                    covers = response.json().get("covers") or []
                    if covers:
                        new_cover_id = str(covers[0])
            except Exception:
                pass
    else:
        from mention_kinds import lookup_by_kind

        match = lookup_by_kind(kind, lookup_title)
        if match:
            new_cover_id = match.get("cover_id")
            new_cover_url = match.get("cover_url")
            new_ol = match.get("open_library_id")
            new_author = match.get("author")
            new_title = match.get("title")
        # Franchise films often have an OL work id from older imports — use its cover
        if not new_cover_id and ol_id and str(ol_id).startswith("/works/"):
            try:
                response = requests.get(f"https://openlibrary.org{ol_id}.json", timeout=20)
                if response.ok:
                    covers = response.json().get("covers") or []
                    if covers:
                        new_cover_id = str(covers[0])
            except Exception:
                pass

    if not new_cover_id and not new_cover_url:
        new_cover_url = lookup_wikipedia_thumb(lookup_title)

    if not new_cover_id and not new_cover_url:
        return False

    if has_cover_url:
        cursor.execute(
            """
            UPDATE books
            SET cover_id = COALESCE(?, cover_id),
                cover_url = COALESCE(?, cover_url),
                open_library_id = COALESCE(?, open_library_id),
                title = CASE
                    WHEN ? IS NOT NULL AND (title IS NULL OR TRIM(title) = '' OR title LIKE '%(EPUB)%')
                    THEN ? ELSE title END,
                author = CASE
                    WHEN ? IS NOT NULL AND (author IS NULL OR author = 'Unknown') THEN ?
                    ELSE author
                END,
                verified = 1
            WHERE id = ?
            """,
            (
                new_cover_id,
                new_cover_url,
                new_ol,
                new_title,
                new_title,
                new_author,
                new_author,
                book_id,
            ),
        )
    else:
        cursor.execute(
            """
            UPDATE books
            SET cover_id = COALESCE(?, cover_id),
                open_library_id = COALESCE(?, open_library_id),
                verified = 1
            WHERE id = ?
            """,
            (new_cover_id, new_ol, book_id),
        )
    conn.commit()
    return True


def backfill_missing_covers(limit: int = 50, quiet: bool = True) -> Dict:
    """Look up covers for works that are missing them (any kind)."""
    init_database(quiet=True)
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(books)")
    cols = {row[1] for row in cursor.fetchall()}
    if "cover_url" in cols:
        where = "(cover_id IS NULL OR cover_id = '') AND (cover_url IS NULL OR cover_url = '')"
    else:
        where = "(cover_id IS NULL OR cover_id = '')"
    cursor.execute(
        f"""
        SELECT id, title FROM books
        WHERE {where}
        ORDER BY CASE IFNULL(kind,'book')
            WHEN 'book' THEN 0 WHEN 'movie' THEN 1 WHEN 'music' THEN 2 ELSE 3 END, id
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    updated = []
    failed = []
    for book_id, title in rows:
        try:
            if ensure_cover_for_book(book_id, cursor, conn):
                updated.append(title)
                if not quiet:
                    print(f"  Cover: {title}")
            else:
                failed.append(title)
        except Exception as e:
            failed.append(f"{title} ({e})")
    conn.close()
    return {
        "checked": len(rows),
        "updated": updated,
        "failed": failed,
        "message": f"Updated {len(updated)} cover(s).",
    }


ProgressCallback = Callable[[Dict], None]


def process_pending_files(
    books_dir: Optional[Path] = None,
    progress_callback: Optional[ProgressCallback] = None,
    quiet: bool = True,
    only_filenames: Optional[List[str]] = None,
    force: bool = False,
) -> Dict:
    """
    Process book files. Safe to call from Flask.
    only_filenames: if set, only those basenames (force reprocess when force=True).
    """
    books_dir = books_dir or Path("books")
    books_dir.mkdir(exist_ok=True)
    init_database(quiet=True)

    files = list_book_files(books_dir)
    if only_filenames:
        wanted = set(only_filenames)
        files = [f for f in files if f.name in wanted]

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    pending = []
    for file_path in files:
        file_hash = get_file_hash(file_path)
        if force or not is_file_processed(file_path.name, file_hash, cursor):
            pending.append(file_path)

    result = {
        "status": "done",
        "total": len(pending),
        "processed": 0,
        "skipped": len(files) - len(pending),
        "books_processed": [],
        "errors": [],
        "current_file": None,
        "phase": "idle",
        "message": "Nothing to process" if not pending else "",
    }

    def emit(updates=None):
        if updates:
            result.update(updates)
        if progress_callback:
            progress_callback(dict(result))

    if not pending:
        emit(
            {
                "status": "done",
                "phase": "idle",
                "message": lucien_says("Nothing new — the shelves are already up to date."),
                "agent": "lucien",
            }
        )
        conn.close()
        return result

    emit(
        {
            "status": "running",
            "phase": "starting",
            "message": borges_says(
                f"Opening {len(pending)} book(s). This may take a few minutes."
            ),
            "agent": "borges",
        }
    )

    for idx, file_path in enumerate(pending, 1):
        emit(
            {
                "status": "running",
                "phase": "extracting",
                "current_file": file_path.name,
                "processed": idx - 1,
                "message": borges_says(
                    f"Reading {file_path.name} ({idx}/{len(pending)})"
                ),
                "agent": "borges",
            }
        )

        def on_file_progress(updates):
            emit(updates)

        try:
            title = process_single_file(
                file_path,
                cursor,
                conn,
                quiet=quiet,
                progress_callback=on_file_progress,
                force=force or bool(only_filenames),
            )
            if title:
                result["books_processed"].append(title)
            emit(
                {
                    "processed": idx,
                    "phase": "saving",
                    "message": lucien_says(
                        f"Shelved {file_path.name} ({idx}/{len(pending)})"
                    ),
                    "agent": "lucien",
                }
            )
        except Exception as e:
            error_msg = f"{file_path.name}: {e}"
            result["errors"].append(error_msg)
            emit({"processed": idx, "phase": "error", "message": error_msg})

    conn.close()
    final_status = "error" if result["errors"] and not result["books_processed"] else "done"
    if result["books_processed"]:
        message = lucien_says(
            f"Done — {len(result['books_processed'])} book(s) mapped"
            + (f" ({len(result['errors'])} error(s))" if result["errors"] else "")
        )
        agent = "lucien"
    elif result["errors"]:
        message = borges_says(
            f"Stopped with errors ({len(result['errors'])} error(s))"
        )
        agent = "borges"
    else:
        message = lucien_says("Nothing new to process")
        agent = "lucien"

    emit(
        {
            "status": final_status,
            "phase": "idle",
            "current_file": None,
            "processed": len(pending),
            "message": message,
            "agent": agent,
        }
    )
    return result


def main():
    """CLI entry point."""
    print("Literary Links AI Visualizer - Data Processing")
    print("=" * 50)

    def on_progress(status):
        if status.get("current_file") and status.get("phase") in (
            "extracting",
            "nlp",
            "open_library",
            "reading",
        ):
            print(status.get("message", ""))

    result = process_pending_files(progress_callback=on_progress, quiet=False)

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM books")
    total_books = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM book_references")
    total_references = cursor.fetchone()[0]
    conn.close()

    print("=" * 50)
    print("Processing complete!")
    print(f"Total books in database: {total_books}")
    print(f"Total references in database: {total_references}")
    print(f"Books processed this run: {result['books_processed']}")
    if result["errors"]:
        print(f"Errors: {result['errors']}")
    print()
    print("You can now run the web application with: python app.py")


if __name__ == "__main__":
    main()
