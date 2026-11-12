#!/usr/bin/env python3
"""
Literary Links AI Visualizer - Data Processing Script
Processes book text files to extract references and populate the SQLite database.
Importable by the Flask app for web-driven processing.
"""

import hashlib
import html
import json
import os
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


DEFAULT_SPACY_MODEL = "en_core_web_trf"
SMALL_SPACY_MODEL = "en_core_web_sm"
_nlp_model_name: Optional[str] = None


def spacy_model_name() -> str:
    """The model in use, or the one that will be tried first."""
    return _nlp_model_name or os.environ.get("BOOKS_SPACY_MODEL", DEFAULT_SPACY_MODEL)


def get_nlp():
    """
    Lazy-load spaCy so importing this module does not require it.

    BOOKS_SPACY_MODEL picks the model. The transformer model is the most
    accurate and about 500 MB; with the Judge doing the quality work, the
    12 MB small model is a reasonable choice, and it is used automatically
    when the configured model is not installed.
    """
    global nlp, _nlp_model_name
    if nlp is not None:
        return nlp
    try:
        import spacy
    except ImportError as e:
        raise RuntimeError("spaCy is not installed. Run: pip install spacy") from e

    wanted = os.environ.get("BOOKS_SPACY_MODEL", DEFAULT_SPACY_MODEL)
    for name in dict.fromkeys([wanted, SMALL_SPACY_MODEL]):
        try:
            nlp = spacy.load(name)
            _nlp_model_name = name
            return nlp
        except OSError:
            continue
    raise RuntimeError(
        f"No spaCy model found. Run: python -m spacy download {wanted}"
        f" (or the small one: python -m spacy download {SMALL_SPACY_MODEL})"
    )


TITLE_STOPWORDS = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for"}
MIN_VERIFY_SCORE = 0.72
STRICT_TITLE_SCORE = 0.95
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
CONTEXT_CHARS = 160


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

    if "user_edited" not in columns:
        # 1 = title/author were set by hand on the manage page; never overwrite
        cursor.execute("ALTER TABLE books ADD COLUMN user_edited INTEGER DEFAULT 0")

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
    if "raw_title" not in ref_cols:
        # The wording found in the text, before it was matched to a work
        cursor.execute("ALTER TABLE book_references ADD COLUMN raw_title TEXT")
    if "manner" not in ref_cols:
        # How the passage uses the work: read, quoted, recommended, ...
        cursor.execute("ALTER TABLE book_references ADD COLUMN manner TEXT")
    for col in ("audit", "audit_reason"):
        # The Auditor's finding on the finished link: ok, fixed, unsure
        if col not in ref_cols:
            cursor.execute(f"ALTER TABLE book_references ADD COLUMN {col} TEXT")
    if "confirmed" not in ref_cols:
        # 1 = a person confirmed this link in the review queue; survives re-extraction
        cursor.execute("ALTER TABLE book_references ADD COLUMN confirmed INTEGER DEFAULT 0")

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

    # Quotes saved by earlier versions still hold HTML entities (&#13;, &amp;)
    cursor.execute(
        "SELECT rowid, context FROM book_references WHERE context LIKE '%&%;%'"
    )
    for rowid, context in cursor.fetchall():
        cursor.execute(
            "UPDATE book_references SET context = ? WHERE rowid = ?",
            (clean_context(context), rowid),
        )

    cursor.execute("PRAGMA table_info(skipped_mentions)")
    skip_cols = {row[1] for row in cursor.fetchall()}
    for col in ("context", "suggested_title", "suggested_author"):
        if col not in skip_cols:
            cursor.execute(f"ALTER TABLE skipped_mentions ADD COLUMN {col} TEXT")

    # A person's corrections, replayed on every re-extraction.
    # raw_key is the normalized wording from the text, or "#<book id>" to reject
    # one specific target for this source whatever the wording.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reference_overrides (
            id INTEGER PRIMARY KEY,
            source_book_id INTEGER NOT NULL,
            raw_key TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('reject', 'link')),
            target_book_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_book_id, raw_key)
        )
        """
    )

    cursor.execute("PRAGMA table_info(reference_overrides)")
    if "made_by" not in {row[1] for row in cursor.fetchall()}:
        # 'you' for decisions on /review, 'auditor' for the Auditor's
        cursor.execute("ALTER TABLE reference_overrides ADD COLUMN made_by TEXT DEFAULT 'you'")

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

    # spaCy's candidates per exact file, so re-judging skips the slow NLP pass
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_cache (
            file_hash TEXT PRIMARY KEY,
            extractor_version INTEGER NOT NULL,
            candidates TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute("PRAGMA table_info(processed_files)")
    if "book_id" not in {row[1] for row in cursor.fetchall()}:
        # Which library row this file is; replaces guessing from the filename
        cursor.execute("ALTER TABLE processed_files ADD COLUMN book_id INTEGER")

    merged = deduplicate_books(cursor, conn, quiet=quiet)
    if merged and not quiet:
        print(f"Merged {merged} duplicate book row(s)")

    try:
        repaired = repair_owned_books(cursor, conn)
        if repaired and not quiet:
            print(f"Corrected title/author on {repaired} library book(s) from file metadata")
    except Exception as e:
        if not quiet:
            print(f"Library metadata repair skipped: {e}")

    pruned = prune_orphan_works(cursor)
    if pruned and not quiet:
        print(f"Removed {pruned} work(s) no longer linked to anything")

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


def prune_orphan_works(cursor) -> int:
    """
    Remove works that nothing points at any more (their links were rejected or
    re-matched). Library books and anything a correction refers to are kept.
    """
    cursor.execute(
        """
        DELETE FROM books
        WHERE id NOT IN (SELECT source_book_id FROM book_references)
          AND id NOT IN (SELECT referenced_book_id FROM book_references)
          AND id NOT IN (SELECT source_book_id FROM skipped_mentions)
          AND id NOT IN (SELECT book_id FROM processed_files WHERE book_id IS NOT NULL)
          AND id NOT IN (SELECT source_book_id FROM reference_overrides)
          AND id NOT IN (
              SELECT target_book_id FROM reference_overrides WHERE target_book_id IS NOT NULL
          )
        """
    )
    return cursor.rowcount


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
    # Keep people's corrections and file ownership pointing at the surviving row
    for statement in (
        "UPDATE OR IGNORE reference_overrides SET source_book_id = ? WHERE source_book_id = ?",
        "UPDATE reference_overrides SET target_book_id = ? WHERE target_book_id = ?",
        "UPDATE processed_files SET book_id = ? WHERE book_id = ?",
    ):
        try:
            cursor.execute(statement, (new_id, old_id))
        except sqlite3.OperationalError:
            pass  # table/column not created yet on a very old database
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

    # One title contained in the other, as whole words: "if" is not inside
    # "tom swift" just because the letters happen to be there.
    if f" {na} " in f" {nb} " or f" {nb} " in f" {na} ":
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


def clean_text(text: str) -> str:
    """Decode HTML entities left over from EPUB markup and tidy whitespace."""
    if not text:
        return ""
    # Some EPUBs are double-escaped (&amp;#13;), so unescape twice.
    text = html.unescape(html.unescape(text))
    return text.replace("\r", " ").replace("\xa0", " ")


def clean_context(text: Optional[str]) -> Optional[str]:
    """Quote shown beside a reference: entities decoded, whitespace collapsed."""
    if not text:
        return text
    return re.sub(r"\s+", " ", clean_text(text)).strip()


_AUTHOR_NOISE = {
    "de", "da", "di", "du", "van", "von", "der", "den", "la", "le",
    "of", "the", "and", "jr", "sr", "st",
}


def author_tokens(name: Optional[str]) -> set:
    """Significant lowercase name tokens (initials and particles dropped)."""
    if not name:
        return set()
    text = re.sub(r"[^\w\s]", " ", name.lower())
    return {t for t in text.split() if len(t) > 1 and t not in _AUTHOR_NOISE}


def author_matches(hint: Optional[str], candidates) -> bool:
    """
    True when any candidate author plausibly is the hinted author.
    The surname must agree (or the names share enough tokens), so "Sun Tzu"
    matches "孙武 (Sun Tzu)" and "Dickens" matches "Charles Dickens".
    """
    want = author_tokens(hint)
    if not want:
        return False
    if isinstance(candidates, str) or candidates is None:
        candidates = [candidates]
    words = re.sub(r"[^\w\s]", " ", (hint or "").lower()).split()
    surname = words[-1] if words else ""
    for cand in candidates:
        have = author_tokens(cand)
        if not have:
            continue
        if len(surname) > 1 and surname in have:
            return True
        shared = want & have
        if shared and (len(shared) >= 2 or len(want) == 1 or len(have) == 1):
            return True
    return False


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


def _ol_doc_to_match(doc: Dict, score: float) -> Dict:
    author_names = doc.get("author_name") or []
    cover_ids = doc.get("cover_i")
    title = doc.get("title") or ""
    return {
        "title": title,
        "author": author_names[0] if author_names else "Unknown",
        "open_library_id": doc.get("key") or f"ol_{normalize_title(title)}",
        "match_score": round(score, 3),
        "cover_id": str(cover_ids) if cover_ids is not None else None,
        "first_publish_year": doc.get("first_publish_year"),
    }


LOOKUP_CACHE_FILE = "lookup_cache.db"
LOOKUP_CACHE_DAYS = 30
_OL_FIELDS = ("key", "title", "author_name", "first_publish_year", "cover_i", "edition_count")


def _lookup_cache():
    """
    A small SQLite file of its own: the main database is mid-transaction while
    mentions are being filed, and a second writer there would be locked out.
    """
    conn = sqlite3.connect(LOOKUP_CACHE_FILE, timeout=5)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lookups (
            key TEXT PRIMARY KEY,
            docs TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
        """
    )
    return conn


def _ol_search(params: Dict) -> List[Dict]:
    """Open Library search, remembered for a month so a re-judge is not one request per mention."""
    import time

    key = json.dumps(params, sort_keys=True, ensure_ascii=False)
    cache = None
    try:
        cache = _lookup_cache()
        row = cache.execute(
            "SELECT docs, fetched_at FROM lookups WHERE key = ?", (key,)
        ).fetchone()
        if row and time.time() - row[1] < LOOKUP_CACHE_DAYS * 86400:
            cache.close()
            return json.loads(row[0])
    except (sqlite3.Error, ValueError):
        cache = None  # the cache is a convenience; never let it break a lookup

    response = requests.get(
        "https://openlibrary.org/search.json",
        params={**params, "limit": 10},
        timeout=30,
    )
    response.raise_for_status()
    docs = [
        {field: doc[field] for field in _OL_FIELDS if field in doc}
        for doc in (response.json().get("docs") or [])
    ]
    if cache is not None:
        try:
            cache.execute(
                "INSERT OR REPLACE INTO lookups (key, docs, fetched_at) VALUES (?, ?, ?)",
                (key, json.dumps(docs, ensure_ascii=False), time.time()),
            )
            cache.commit()
            cache.close()
        except sqlite3.Error:
            pass
    return docs


def _too_new(doc: Dict, max_year: Optional[int]) -> bool:
    """A work can't be mentioned by a book published before it existed."""
    if not max_year:
        return False
    year = doc.get("first_publish_year")
    return isinstance(year, int) and year > max_year + 1


def _lookup_open_library_by_author(
    title: str, author: str, max_year: Optional[int]
) -> Optional[Dict]:
    """
    Author-corroborated lookup. Because the author has to agree, the title may
    match more loosely than in a title-only search ("Clarendon's Rebellion" vs
    "The History of the Rebellion and Civil Wars in England").
    """
    best = None
    best_score = 0.0
    for params in ({"title": title, "author": author}, {"q": f"{title} {author}"}):
        for doc in _ol_search(params):
            candidate_title = doc.get("title") or ""
            if not candidate_title or _too_new(doc, max_year):
                continue
            if not author_matches(author, doc.get("author_name") or []):
                continue
            score = max(
                title_similarity(title, candidate_title),
                0.9 * token_coverage(title, candidate_title),
                0.9 * token_coverage(candidate_title, title),
            )
            if score < 0.5:
                continue
            # Prefer the widely published work when titles tie (not a study guide)
            rank = score + min(doc.get("edition_count") or 0, 50) / 5000.0
            if rank > best_score:
                best_score = rank
                best = _ol_doc_to_match(doc, min(1.0, score))
        if best:
            break
    return best


def lookup_open_library(
    title: str,
    author: Optional[str] = None,
    max_year: Optional[int] = None,
    strict_title: bool = False,
) -> Optional[Dict]:
    """
    Search Open Library and return the best verified match, or None.
    Result keys: title, author, open_library_id, match_score, cover_id,
    first_publish_year.

    author: when given (the Judge's reading of the passage), only works by that
            author are accepted. There is no silent fallback to a same-titled
            work by someone else: that was the main source of wrong links.
    max_year: reject works first published after this year.
    """
    if author and author.strip() and author.strip().lower() != "unknown":
        try:
            return _lookup_open_library_by_author(title, author.strip(), max_year)
        except Exception as e:
            print(f"Open Library lookup failed for '{title}' by {author}: {e}")
            return None

    if not is_plausible_title_query(title):
        return None

    try:
        docs = _ol_search({"title": title})

        def pick_best(candidates):
            query_words = normalize_title(title).split()
            short_query = len(query_words) <= 2
            local_best = None
            local_score = 0.0
            for doc in candidates:
                candidate_title = doc.get("title") or ""
                if not candidate_title or _too_new(doc, max_year):
                    continue
                score = title_similarity(title, candidate_title)
                coverage = token_coverage(title, candidate_title)
                if coverage < (1.0 if short_query else 0.75):
                    continue
                if short_query and score < 0.92:
                    continue
                if doc.get("author_name"):
                    score = min(1.0, score + 0.02)
                if score > local_score:
                    local_score = score
                    local_best = _ol_doc_to_match(doc, score)
            return local_best

        best = pick_best(docs)
        if not best:
            best = pick_best(_ol_search({"q": title}))
    except Exception as e:
        print(f"Open Library lookup failed for '{title}': {e}")
        return None

    if not best:
        return None
    if strict_title:
        # No author to corroborate the match, so the title has to be the same
        return best if best["match_score"] >= STRICT_TITLE_SCORE else None
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


def _find_local_book(
    title: str, cursor, verified_only: bool = False, min_score: float = MIN_VERIFY_SCORE
) -> Optional[int]:
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
    if best_id is not None and best_score >= min_score:
        return best_id
    return None


def _file_from_judge(cursor, conn, title: str, author: str, kind: str) -> int:
    """
    File a work on the Judge's word when no catalogue has it (or, for music,
    when the catalogue would name a random performer instead of the composer).
    The id marks where it came from, so the page can say so.
    """
    who = "_".join(sorted(author_tokens(author)))
    judge_id = f"judge_{kind}_{normalize_title(title) or title.strip().lower()}_{who}".rstrip("_")
    cursor.execute("SELECT id FROM books WHERE open_library_id = ?", (judge_id,))
    existing = cursor.fetchone()
    if existing:
        return existing[0]
    label = author or {"movie": "Film / TV", "music": "Traditional", "book": "Anonymous"}.get(
        kind, "Unknown"
    )
    cursor.execute(
        """
        INSERT INTO books (title, author, open_library_id, verified, match_score, kind)
        VALUES (?, ?, ?, 1, NULL, ?)
        """,
        (title, label, judge_id, kind),
    )
    conn.commit()
    return cursor.lastrowid


def _upsert_verified_match(
    cursor, conn, match: Dict, kind: str = "book", strict_author: bool = False
) -> Optional[int]:
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
        "SELECT id, author FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?)) AND IFNULL(kind, 'book') = ?",
        (match["title"], kind),
    )
    same_title = cursor.fetchone()
    # With a known author, a same-titled row by someone else is a different work:
    # leave it alone and file this one separately.
    if same_title and strict_author and not author_matches(match.get("author"), same_title[1]):
        same_title = None
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
    author: Optional[str] = None,
    max_year: Optional[int] = None,
    judged: bool = False,
) -> Optional[int]:
    """
    Get or create a work via local cache then domain lookup.

    judged: the Judge read the passage and vouches that this is a real work.
            - With an author, a work no catalogue finds is filed on its word.
            - Without one there is nothing to corroborate a loose title match,
              so only an identical title is accepted.

    author / max_year: hints from the Judge. With an author, local rows and Open
    Library hits must be by that author; max_year rejects works newer than the
    book that mentions them.

    kind: book (Open Library), movie, music, cocktail.
    Owned library books should call with require_verified=False, kind='book'.
    """
    kind = (kind or "book").lower()
    if kind not in ("book", "movie", "music", "cocktail"):
        kind = "book"

    # Prefer local match of the same kind
    author_hint = (author or "").strip()
    if author_hint.lower() in ("unknown", "anonymous", "traditional"):
        author_hint = ""
    use_author = bool(author_hint) and (kind == "book" or judged)
    min_score = STRICT_TITLE_SCORE if judged and not author_hint else MIN_VERIFY_SCORE

    cursor.execute(
        """
        SELECT id, title, verified, kind, author FROM books
        WHERE IFNULL(kind, 'book') = ?
        """,
        (kind,),
    )
    best_id = None
    best_score = 0.0
    for book_id, existing_title, verified, _k, existing_author in cursor.fetchall():
        if require_verified and not verified:
            continue
        if use_author and not author_matches(author_hint, existing_author):
            continue
        score = title_similarity(title, existing_title)
        if score > best_score:
            best_score = score
            best_id = book_id
    if best_id is not None and best_score >= min_score:
        return best_id

    if kind != "book":
        from mention_kinds import lookup_by_kind

        # A recording search answers "Messiah" with whoever recorded something
        # called that, and a film search answers "The Wizard of Oz" with a 1993
        # video game. For judged music and films keep the Judge's answer: the
        # composer or director when it named one, otherwise no creator at all
        # (a traditional tune does not belong to the first band that recorded it).
        if judged and kind in ("music", "movie"):
            return _file_from_judge(cursor, conn, title, author_hint, kind)
        match = lookup_by_kind(kind, title)
        if match:
            return _upsert_verified_match(cursor, conn, match, kind=kind)
        if judged:
            return _file_from_judge(cursor, conn, title, author_hint, kind)
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
    if not use_author:
        local_id = _find_local_book(
            title, cursor, verified_only=require_verified, min_score=min_score
        )
        if local_id is not None:
            return local_id

        if not require_verified:
            local_any = _find_local_book(title, cursor, verified_only=False, min_score=min_score)
            if local_any is not None:
                return local_any

    match = lookup_open_library(
        title,
        author=author_hint if use_author else None,
        max_year=max_year,
        strict_title=judged and not use_author,
    )
    if match:
        match["kind"] = "book"
        return _upsert_verified_match(
            cursor, conn, match, kind="book", strict_author=use_author
        )
    if judged:
        # Real, by the Judge's reading, but absent from the catalogue. That is
        # the normal case for anonymous and official texts (a proclamation, a
        # commission's report, the Bible), which have no author to search by.
        # The Auditor checks these afterwards.
        return _file_from_judge(cursor, conn, title, author_hint, "book")

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
        (title, author_hint or "Unknown", manual_id),
    )
    conn.commit()
    return cursor.lastrowid


def override_key(raw_title: Optional[str]) -> str:
    """Key a person's correction by the wording found in the text."""
    return normalize_title(raw_title or "") or (raw_title or "").strip().lower()


def _load_overrides(cursor, source_book_id: int) -> Dict[str, tuple]:
    cursor.execute(
        "SELECT raw_key, action, target_book_id FROM reference_overrides WHERE source_book_id = ?",
        (source_book_id,),
    )
    return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}


def _override_makers(cursor, source_book_id: int) -> Dict[str, str]:
    """Who made each correction for this book: 'you' or 'auditor'."""
    cursor.execute(
        "SELECT raw_key, IFNULL(made_by, 'you') FROM reference_overrides WHERE source_book_id = ?",
        (source_book_id,),
    )
    return dict(cursor.fetchall())


def _save_override(
    cursor, source_book_id: int, raw_key: str, action: str, target_id=None, made_by: str = "you"
):
    """Remember a correction. A person's decision is never replaced by the Auditor's."""
    if not raw_key:
        return
    if made_by != "you":
        cursor.execute(
            "SELECT IFNULL(made_by, 'you') FROM reference_overrides WHERE source_book_id = ? AND raw_key = ?",
            (source_book_id, raw_key),
        )
        existing = cursor.fetchone()
        if existing and existing[0] == "you":
            return
    cursor.execute(
        """
        INSERT INTO reference_overrides (source_book_id, raw_key, action, target_book_id, made_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_book_id, raw_key)
        DO UPDATE SET action = excluded.action, target_book_id = excluded.target_book_id,
                      made_by = excluded.made_by
        """,
        (source_book_id, raw_key, action, target_id, made_by),
    )


def _rejected_reason(maker: Optional[str]) -> str:
    return "Rejected by the Auditor" if maker == "auditor" else "Rejected by you"


def _record_skip(
    cursor,
    source_book_id: int,
    raw_title: str,
    reason: str,
    context: Optional[str] = None,
    suggested_title: Optional[str] = None,
    suggested_author: Optional[str] = None,
):
    cursor.execute(
        """
        INSERT INTO skipped_mentions
            (source_book_id, raw_title, reason, context, suggested_title, suggested_author)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            source_book_id,
            raw_title,
            reason,
            clean_context(context),
            suggested_title or None,
            suggested_author or None,
        ),
    )


def _link_reference(
    cursor,
    source_book_id: int,
    referenced_book_id: int,
    context: Optional[str],
    raw_title: Optional[str],
    confirmed: bool = False,
    manner: Optional[str] = None,
) -> bool:
    """Insert the edge if new. Returns True when a row was added."""
    context = clean_context(context)
    manner = manner or None
    cursor.execute(
        "SELECT 1 FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?",
        (source_book_id, referenced_book_id),
    )
    if cursor.fetchone():
        cursor.execute(
            """
            UPDATE book_references
            SET context = COALESCE(context, ?),
                raw_title = COALESCE(raw_title, ?),
                manner = COALESCE(?, manner),
                confirmed = MAX(IFNULL(confirmed, 0), ?)
            WHERE source_book_id = ? AND referenced_book_id = ?
            """,
            (
                context,
                raw_title,
                manner,
                1 if confirmed else 0,
                source_book_id,
                referenced_book_id,
            ),
        )
        return False
    cursor.execute(
        """
        INSERT INTO book_references
            (source_book_id, referenced_book_id, context, raw_title, confirmed, manner)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            source_book_id,
            referenced_book_id,
            context,
            raw_title,
            1 if confirmed else 0,
            manner,
        ),
    )
    return True


def _store_references(
    references_found,
    source_book_id,
    cursor,
    conn,
    quiet=False,
    progress_callback=None,
    judge=None,
):
    """
    references_found: iterable of str titles OR dicts with title/context.

    Order of authority for each mention:
      1. a person's correction (reference_overrides),
      2. the Judge's verdict (is it a work, which kind, by whom),
      3. the older keyword heuristics, when the Judge is off or didn't answer.

    judge: callable(source_title, source_author, mentions, progress_callback)
           returning judge.judge_mentions' result; defaults to the real Judge.
    """
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
    items = [i for i in items if (i.get("title") or "").strip()]

    overrides = _load_overrides(cursor, source_book_id)
    makers = _override_makers(cursor, source_book_id)
    cursor.execute("SELECT title FROM books WHERE id = ?", (source_book_id,))
    own_title = ((cursor.fetchone() or [""])[0] or "").strip()

    # —— The Judge reads every candidate in its passage (cocktails come from a
    # curated lexicon and people's corrections are already decided) ——
    verdicts: Dict[int, Dict] = {}
    for pos, item in enumerate(items):
        if isinstance(item.get("verdict"), dict):
            verdicts[pos] = item["verdict"]  # the Reader already ruled on these
    to_judge = [
        (pos, item)
        for pos, item in enumerate(items)
        if item.get("kind_hint") != "cocktail"
        and pos not in verdicts
        and override_key(item.get("title")) not in overrides
    ]
    source_year = None
    judged = bool(verdicts)
    if to_judge:
        if judge is None:
            from judge import judge_mentions as judge
        cursor.execute("SELECT title, author FROM books WHERE id = ?", (source_book_id,))
        row = cursor.fetchone() or ("", "")
        try:
            result = judge(
                row[0] or "",
                row[1] or "",
                [item for _pos, item in to_judge],
                progress_callback=progress_callback,
            )
        except Exception as e:
            result = None
            if not quiet:
                print(f"  The Judge failed, using basic matching: {e}")
        if result is not None:
            judged = True
            source_year = result.get("source_year")
            for local_idx, verdict in (result.get("verdicts") or {}).items():
                verdicts[to_judge[local_idx][0]] = verdict
        elif progress_callback:
            progress_callback(
                {
                    "phase": "open_library",
                    "message": lucien_says(
                        "The Judge is away (no Claude credentials) — using basic title matching"
                    ),
                    "agent": "lucien",
                }
            )

    total = len(items)
    for idx, item in enumerate(items, 1):
        raw_title = clean_text(item.get("title") or "").strip()
        # Italic and quoted spans are a wide net (emphasis, ship names, foreign
        # words). Without the Judge to rule on them they would only add noise.
        if (
            item.get("source") in ("markup", "quote")
            and (idx - 1) not in verdicts
            and override_key(raw_title) not in overrides
        ):
            continue
        context = item.get("context")
        try:
            key = override_key(raw_title)
            action, target_id = overrides.get(key, (None, None))
            if action == "reject":
                skipped += 1
                _record_skip(
                    cursor, source_book_id, raw_title, _rejected_reason(makers.get(key)), context
                )
                continue
            if action == "link" and target_id:
                cursor.execute("SELECT 1 FROM books WHERE id = ?", (target_id,))
                if cursor.fetchone() and target_id != source_book_id:
                    by_person = makers.get(key, "you") == "you"
                    if _link_reference(
                        cursor, source_book_id, target_id, context, raw_title, confirmed=by_person
                    ):
                        added += 1
                    if not by_person:
                        # the Auditor re-filed this one before; no need to ask again
                        cursor.execute(
                            """
                            UPDATE book_references SET audit = 'fixed'
                            WHERE source_book_id = ? AND referenced_book_id = ? AND audit IS NULL
                            """,
                            (source_book_id, target_id),
                        )
                    continue

            verdict = verdicts.get(idx - 1)
            lookup_title = raw_title
            author_hint = None
            if verdict is not None:
                if not verdict.get("is_reference") or verdict.get("kind") == "other":
                    skipped += 1
                    reason = (verdict.get("reason") or "not a reference to a work").strip()
                    _record_skip(
                        cursor, source_book_id, raw_title, f"Judge: {reason}", context
                    )
                    if not quiet:
                        print(f"  Skipped (Judge: {reason}): {raw_title}")
                    continue
                kind = verdict["kind"]
                lookup_title = (verdict.get("canonical_title") or "").strip() or raw_title
                author_hint = (verdict.get("author") or "").strip() or None
                # The film or stage version of the book, named on its own jacket,
                # is the book talking about itself, not a link to another work
                bare_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", lookup_title).strip()
                if own_title and title_similarity(bare_title, own_title) >= 0.95:
                    skipped += 1
                    _record_skip(
                        cursor,
                        source_book_id,
                        raw_title,
                        "The book itself, or a version of it",
                        context,
                    )
                    continue
            else:
                kind = item.get("kind_hint") or classify_kind(raw_title, context)
                if kind not in ("book", "movie", "music", "cocktail"):
                    kind = classify_kind(raw_title, context)

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
                            f'{agent_verb} "{lookup_title}" ({idx}/{total})'
                        ),
                        "agent": "lucien",
                    }
                )

            # The plausibility filter guards the title-only path; the Judge has
            # already vouched for judged mentions (it was dropping "Lean In").
            if kind == "book" and verdict is None:
                reason = skip_reason_for_title(raw_title)
                if reason:
                    skipped += 1
                    _record_skip(cursor, source_book_id, raw_title, reason, context)
                    if not quiet:
                        print(f"  Skipped ({reason}): {raw_title}")
                    continue

            referenced_book_id = get_or_create_book(
                lookup_title,
                cursor,
                conn,
                require_verified=True,
                kind=kind,
                context=context,
                author=author_hint,
                # With an author the match is already pinned down, and later
                # editions legitimately mention later works; the date check
                # only guards title-only matches.
                max_year=source_year if kind == "book" and not author_hint else None,
                judged=verdict is not None,
            )
            if referenced_book_id is None:
                skipped += 1
                if verdict is not None and kind == "book":
                    by = f" by {author_hint}" if author_hint else ""
                    reason = f"Judge says it is “{lookup_title}”{by}, but no catalogue match"
                else:
                    reason = {
                        "book": "No strong Open Library / Google Books match",
                        "movie": "No strong film match",
                        "music": "No strong music match",
                        "cocktail": "Not in cocktail lexicon",
                    }.get(kind, "No strong match")
                _record_skip(
                    cursor,
                    source_book_id,
                    raw_title,
                    reason,
                    context,
                    suggested_title=lookup_title if verdict is not None else None,
                    suggested_author=author_hint,
                )
                if not quiet:
                    print(f"  Skipped unverified ({kind}): {raw_title}")
                continue
            if source_book_id == referenced_book_id:
                continue
            if overrides.get(f"#{referenced_book_id}", (None, None))[0] == "reject":
                skipped += 1
                _record_skip(
                    cursor,
                    source_book_id,
                    raw_title,
                    _rejected_reason(makers.get(f"#{referenced_book_id}")),
                    context,
                )
                continue
            try:
                ensure_cover_for_book(referenced_book_id, cursor, conn)
            except Exception:
                pass
            if _link_reference(
                cursor,
                source_book_id,
                referenced_book_id,
                context,
                raw_title,
                manner=(verdict or {}).get("manner"),
            ):
                added += 1
                if not quiet:
                    print(f"  Added verified {kind}: {lookup_title}")
        except Exception as e:
            if not quiet:
                print(f"  Error processing reference '{raw_title}': {e}")
    conn.commit()
    if not quiet:
        how = "judged by Claude" if judged else "basic matching"
        print(
            f"  Found {len(items)} candidates ({how}); added {added} verified, skipped {skipped}"
        )
    return {"added": added, "skipped": skipped, "judged": judged}


def audit_book(
    source_book_id: int,
    cursor,
    conn,
    progress_callback=None,
    auditor=None,
    quiet: bool = True,
) -> Dict:
    """
    Let the Auditor check this book's finished links, and act on what it finds:
    mark the right ones, re-file the wrong ones, remove what is not a reference.
    Links a person confirmed, and links already audited, are left alone.

    auditor: callable(source_title, source_author, links, progress_callback)
             returning auditor.audit_links' result; defaults to the real one.
    """
    summary = {"ran": False, "ok": 0, "fixed": 0, "removed": 0, "unsure": 0}
    if auditor is None:
        import auditor as auditor_module

        if not auditor_module.auditor_enabled():
            return summary
        auditor = auditor_module.audit_links

    cursor.execute(
        """
        SELECT r.referenced_book_id, b.title, b.author, IFNULL(b.kind, 'book'),
               b.open_library_id, r.raw_title, r.context, r.manner
        FROM book_references r JOIN books b ON b.id = r.referenced_book_id
        WHERE r.source_book_id = ? AND IFNULL(r.confirmed, 0) = 0 AND r.audit IS NULL
        ORDER BY b.title
        """,
        (source_book_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return summary
    cursor.execute("SELECT title, author FROM books WHERE id = ?", (source_book_id,))
    source = cursor.fetchone() or ("", "")
    links = [
        {
            "title": title,
            "author": author,
            "kind": kind,
            "raw_title": raw_title,
            "context": context,
            "judge_filed": str(ol_id or "").startswith("judge_"),
        }
        for _target, title, author, kind, ol_id, raw_title, context, _manner in rows
    ]
    verdicts = auditor(source[0] or "", source[1] or "", links, progress_callback=progress_callback)
    if verdicts is None:
        return summary
    summary["ran"] = True

    def set_audit(target_id, state, reason):
        cursor.execute(
            """
            UPDATE book_references SET audit = ?, audit_reason = ?
            WHERE source_book_id = ? AND referenced_book_id = ?
            """,
            (state, (reason or "").strip() or None, source_book_id, target_id),
        )

    def remove(target_id, raw_title, context, reason):
        cursor.execute(
            "DELETE FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?",
            (source_book_id, target_id),
        )
        _save_override(cursor, source_book_id, override_key(raw_title), "reject", made_by="auditor")
        _save_override(cursor, source_book_id, f"#{target_id}", "reject", made_by="auditor")
        _record_skip(
            cursor, source_book_id, raw_title or "", f"Rejected by the Auditor: {reason}", context
        )
        summary["removed"] += 1

    for pos, (target_id, title, _author, _kind, _ol, raw_title, context, manner) in enumerate(rows):
        verdict = verdicts.get(pos)
        if not verdict:
            continue
        reason = (verdict.get("reason") or "").strip()
        outcome = verdict.get("verdict")
        if outcome == "right":
            set_audit(target_id, "ok", reason)
            summary["ok"] += 1
        elif outcome == "unsure":
            set_audit(target_id, "unsure", reason)
            summary["unsure"] += 1
        elif outcome == "not_a_reference":
            remove(target_id, raw_title or title, context, reason or "not a reference to a work")
        elif outcome == "wrong_work":
            new_id = None
            correct_title = (verdict.get("correct_title") or "").strip()
            correct_author = (verdict.get("correct_author") or "").strip()
            if correct_title and not correct_author:
                # An anonymous or traditional work. A title-only catalogue search
                # is how these went wrong in the first place (Proverbs became a
                # commentary on Proverbs), so file the work the Auditor named.
                new_id = _file_from_judge(
                    cursor, conn, correct_title, "", verdict.get("correct_kind") or "book"
                )
            elif correct_title:
                try:
                    new_id = get_or_create_book(
                        correct_title,
                        cursor,
                        conn,
                        require_verified=True,
                        kind=verdict.get("correct_kind") or "book",
                        context=context,
                        author=(verdict.get("correct_author") or "").strip() or None,
                        judged=True,
                    )
                except Exception:
                    new_id = None
            if new_id == target_id and correct_title:
                # The catalogue only has the wrong record to offer (a retelling of
                # a folk tale, a remake of a show). File the work the Auditor named.
                new_id = _file_from_judge(
                    cursor,
                    conn,
                    correct_title,
                    (verdict.get("correct_author") or "").strip(),
                    verdict.get("correct_kind") or "book",
                )
            if new_id == target_id:
                set_audit(target_id, "unsure", reason)
                summary["unsure"] += 1
            elif new_id and new_id != source_book_id:
                cursor.execute(
                    "DELETE FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?",
                    (source_book_id, target_id),
                )
                _link_reference(cursor, source_book_id, new_id, context, raw_title, manner=manner)
                set_audit(new_id, "fixed", reason)
                _save_override(
                    cursor, source_book_id, override_key(raw_title), "link", new_id, made_by="auditor"
                )
                _save_override(cursor, source_book_id, f"#{target_id}", "reject", made_by="auditor")
                try:
                    ensure_cover_for_book(new_id, cursor, conn)
                except Exception:
                    pass
                summary["fixed"] += 1
                if not quiet:
                    print(f"  Auditor re-filed: {title} -> {correct_title}")
            else:
                remove(target_id, raw_title or title, context, reason or "wrong work")
    prune_orphan_works(cursor)
    conn.commit()
    if not quiet:
        print(
            f"  Auditor: {summary['ok']} right, {summary['fixed']} re-filed, "
            f"{summary['removed']} removed, {summary['unsure']} unsure"
        )
    return summary


def rescue_dropped_works(quiet: bool = False) -> int:
    """
    Link mentions that were dropped only because no catalogue had the work,
    although the Judge had identified it. They are filed on the Judge's word
    and left unaudited, so the Auditor looks at each one next.
    Used after the matching rules change, to avoid re-reading every book.
    """
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, source_book_id, raw_title, context, suggested_title, suggested_author
        FROM skipped_mentions
        WHERE reason LIKE 'Judge says it is%no catalogue match' AND suggested_title IS NOT NULL
        """
    )
    rescued = 0
    for skip_id, source_id, raw_title, context, title, author in cursor.fetchall():
        key = override_key(raw_title)
        if _load_overrides(cursor, source_id).get(key, (None, None))[0] == "reject":
            continue
        target_id = _file_from_judge(cursor, conn, title, (author or "").strip(), "book")
        if target_id == source_id:
            continue
        if _link_reference(cursor, source_id, target_id, context, raw_title):
            rescued += 1
            if not quiet:
                print(f"  Rescued: {title}")
        cursor.execute("DELETE FROM skipped_mentions WHERE id = ?", (skip_id,))
    conn.commit()
    conn.close()
    return rescued


def audit_library(quiet: bool = False) -> Dict:
    """Audit every library book's unchecked links, without re-reading anything."""
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT b.id, b.title FROM books b
        JOIN book_references r ON r.source_book_id = b.id
        WHERE IFNULL(r.confirmed, 0) = 0 AND r.audit IS NULL
        ORDER BY b.title
        """
    )
    totals = {"ok": 0, "fixed": 0, "removed": 0, "unsure": 0}
    for book_id, title in cursor.fetchall():
        if not quiet:
            print(f"Auditing: {title}")
        result = audit_book(book_id, cursor, conn, quiet=quiet)
        for key in totals:
            totals[key] += result.get(key, 0)
    conn.close()
    return totals


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
            # Wide enough for the Judge (and a reader) to see how the title is used
            ctx_start = max(0, abs_start - CONTEXT_CHARS)
            ctx_end = min(len(text), abs_end + CONTEXT_CHARS)
            context = re.sub(r"\s+", " ", text[ctx_start:ctx_end]).strip()
            title = re.sub(r"\s+", " ", title)
            key = title.lower()
            if key not in mentions:
                mentions[key] = {"title": title, "context": context}
    return list(mentions.values())


# Bump when candidate finding changes, so cached candidates are rebuilt
EXTRACTOR_VERSION = 3

_EMPHASIS_RE = re.compile(r"<(i|em|cite)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)
_ITALIC_SPAN_RE = re.compile(
    r"<span\b[^>]*class=[\"'][^\"']*(?:ital|booktitle|book-title)[^\"']*[\"'][^>]*>(.*?)</span\s*>",
    re.I | re.S,
)
_QUOTED_RE = re.compile(r"[“\"]([A-Z0-9][^“”\"\n]{2,90})[”\"]")
_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "de", "for", "from", "in", "into",
    "la", "le", "nor", "of", "on", "or", "the", "to", "und", "van", "von", "with",
}


def _strip_tags(markup: str) -> str:
    return re.sub(r"\s+", " ", clean_text(re.sub("<[^<]+?>", "", markup))).strip()


def looks_like_a_title(text: str, min_words: int = 1) -> bool:
    """
    Cheap shape test for italic or quoted spans: capitalised like a title, short,
    and not a sentence. It only has to be good enough to hand to the Judge.
    """
    text = (text or "").strip()
    if not (3 <= len(text) <= 90) or not (text[0].isupper() or text[0].isdigit()):
        return False
    if text[-1] in ".,;:!?…—-":
        return False
    words = text.split()
    if not (min_words <= len(words) <= 10):
        return False
    content = [w for w in words if w.strip("’'\"“”()").lower() not in _SMALL_WORDS]
    if not content or not any(len(re.sub(r"\W", "", w)) >= 3 for w in content):
        return False
    capitalised = sum(1 for w in content if w.lstrip("’'\"“(")[:1].isupper())
    return capitalised / len(content) >= 0.6


def _context_for(text: str, needle: str) -> str:
    at = text.find(needle)
    if at == -1:
        return needle
    ctx_start = max(0, at - CONTEXT_CHARS)
    ctx_end = min(len(text), at + len(needle) + CONTEXT_CHARS)
    return re.sub(r"\s+", " ", text[ctx_start:ctx_end]).strip()


_ITALIC_RULE_RE = re.compile(
    r"([^{}]+)\{[^{}]*font-style\s*:\s*(?:italic|oblique)[^{}]*\}", re.I
)
_CLASS_IN_SELECTOR_RE = re.compile(r"\.([A-Za-z_][\w-]*)")


def italic_classes_from_css(css: str) -> List[str]:
    """
    Class names a stylesheet sets in italics. Many EPUBs do not use <i> at all:
    the publisher's tool writes <span class="class_s2US"> and puts the italics
    in the CSS, so the class names have to be read from there.
    """
    names = []
    for rule in _ITALIC_RULE_RE.finditer(css or ""):
        for selector in rule.group(1).split(","):
            # Only the last compound selector styles the element itself
            last = selector.strip().split()[-1] if selector.strip() else ""
            for name in _CLASS_IN_SELECTOR_RE.findall(last):
                if name not in names:
                    names.append(name)
    return names


def extract_markup_mentions(markup: str, text: str, italic_classes=()) -> List[Dict]:
    """
    Titles the publisher set in italics. spaCy misses many of these in running
    prose ("she was reading <i>Middlemarch</i> again"); the markup does not.
    italic_classes: class names the book's stylesheet sets in italics.
    """
    mentions = {}
    spans = [m.group(2) for m in _EMPHASIS_RE.finditer(markup)]
    spans += [m.group(1) for m in _ITALIC_SPAN_RE.finditer(markup)]
    if italic_classes:
        by_class = re.compile(
            r"<(\w+)\b[^>]*class=[\"'][^\"']*\b(?:%s)\b[^\"']*[\"'][^>]*>(.*?)</\1\s*>"
            % "|".join(re.escape(name) for name in italic_classes),
            re.I | re.S,
        )
        spans += [m.group(2) for m in by_class.finditer(markup)]
    for raw in spans:
        title = _strip_tags(raw).strip(" .,;:")
        if not looks_like_a_title(title):
            continue
        key = title.lower()
        if key not in mentions:
            mentions[key] = {
                "title": title,
                "context": _context_for(text, title),
                "source": "markup",
            }
    return list(mentions.values())


def extract_quoted_mentions(text: str) -> List[Dict]:
    """Short Title Case phrases in quotation marks (essays, poems, songs, articles)."""
    mentions = {}
    for match in _QUOTED_RE.finditer(text or ""):
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        if not looks_like_a_title(title, min_words=2):
            continue
        key = title.lower()
        if key not in mentions:
            ctx_start = max(0, match.start() - CONTEXT_CHARS)
            ctx_end = min(len(text), match.end() + CONTEXT_CHARS)
            mentions[key] = {
                "title": title,
                "context": re.sub(r"\s+", " ", text[ctx_start:ctx_end]).strip(),
                "source": "quote",
            }
    return list(mentions.values())


def read_book_file(file_path: Path) -> tuple:
    """Return (plain text, raw markup or '', italic class names) for an EPUB or TXT file."""
    if file_path.suffix.lower() == ".epub":
        if epub is None or ebooklib is None:
            raise RuntimeError("ebooklib is not installed. Run: pip install ebooklib")
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            book = epub.read_epub(str(file_path))
        markup = ""
        css = ""
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                markup += item.get_content().decode("utf-8", errors="ignore") + "\n"
            elif item.get_type() == ebooklib.ITEM_STYLE:
                css += item.get_content().decode("utf-8", errors="ignore") + "\n"
        # <style> blocks inside the pages count too
        css += "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", markup, flags=re.I | re.S))
        return clean_text(re.sub("<[^<]+?>", "", markup)), markup, italic_classes_from_css(css)
    with open(file_path, "r", encoding="utf-8") as f:
        return clean_text(f.read()), "", []


def find_candidates(file_path: Path, progress_callback=None) -> List[Dict]:
    """Every candidate mention in a file: spaCy, italics, quoted titles, cocktails."""
    if progress_callback:
        progress_callback(
            {
                "phase": "reading",
                "message": borges_says(f"Opening {file_path.name}"),
                "agent": "borges",
            }
        )
    text, markup, italic_classes = read_book_file(file_path)
    if progress_callback:
        progress_callback(
            {
                "phase": "loading_model",
                "message": borges_says("Waking up his reading mind…"),
                "agent": "borges",
            }
        )
    get_nlp()
    # spaCy's finds come first so they keep their wording when two sources agree
    return merge_mention_lists(
        extract_work_of_art_mentions(text, progress_callback=progress_callback),
        extract_markup_mentions(markup, text, italic_classes) if markup else [],
        extract_quoted_mentions(text),
        extract_cocktail_mentions(text),
    )


def cached_candidates(file_hash: Optional[str], cursor) -> Optional[List[Dict]]:
    if not file_hash:
        return None
    try:
        cursor.execute(
            "SELECT candidates FROM candidate_cache WHERE file_hash = ? AND extractor_version = ?",
            (file_hash, EXTRACTOR_VERSION),
        )
    except sqlite3.OperationalError:
        return None
    row = cursor.fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def store_candidates(file_hash: Optional[str], candidates: List[Dict], cursor, conn):
    if not file_hash:
        return
    cursor.execute(
        """
        INSERT OR REPLACE INTO candidate_cache (file_hash, extractor_version, candidates)
        VALUES (?, ?, ?)
        """,
        (file_hash, EXTRACTOR_VERSION, json.dumps(candidates, ensure_ascii=False)),
    )
    conn.commit()


def add_reader_mentions(
    mentions: List[Dict],
    file_path: Path,
    source_book_id: int,
    file_hash: Optional[str],
    cursor,
    conn,
    progress_callback=None,
) -> List[Dict]:
    """
    When the Reader is switched on (BOOKS_READER=on), let Claude read the book
    and put its finds ahead of the pattern-based candidates. They arrive with a
    verdict, so they skip the Judge. Cached per file and model like the rest.
    """
    import reader

    if not reader.reader_enabled():
        return mentions
    readings = []
    text = None
    # Each reading is cached on its own, so turning the second one on later
    # does not pay for the first again
    for number in range(1, reader.reader_passes() + 1):
        tag = f"reader{reader.READER_VERSION}" + ("" if number == 1 else f"-pass{number}")
        cache_key = f"{tag}:{reader.reader_model()}:{file_hash}" if file_hash else None
        found = cached_candidates(cache_key, cursor)
        if found is None:
            if text is None:
                text, _markup, _italic = read_book_file(file_path)
            cursor.execute("SELECT title, author FROM books WHERE id = ?", (source_book_id,))
            row = cursor.fetchone() or ("", "")
            found = reader.read_for_mentions(
                row[0] or "",
                row[1] or "",
                text,
                progress_callback=progress_callback,
                overlap=0 if number == 1 else reader.SECOND_PASS_OVERLAP,
            )
            if found is None:
                break  # no credentials, or it failed: carry on with what there is
            store_candidates(cache_key, found, cursor, conn)
        readings.append(found)
    return merge_mention_lists(*readings, mentions)


def process_book_file(
    file_path: Path,
    source_book_id: int,
    cursor,
    conn,
    quiet=False,
    progress_callback=None,
    file_hash: Optional[str] = None,
):
    """
    Find candidates (or reuse the cached ones for this exact file), then let
    corrections, the Judge and Lucien decide what becomes a link. With a cache
    hit the slow spaCy pass is skipped, so re-judging a book takes seconds.
    """
    if not quiet:
        print(f"Processing: {file_path.name}")
    try:
        # Candidates depend on the spaCy model, so the cache is keyed by both
        cache_key = f"{spacy_model_name()}:{file_hash}" if file_hash else None
        mentions = cached_candidates(cache_key, cursor)
        if mentions is None:
            mentions = find_candidates(file_path, progress_callback=progress_callback)
            # the small model may have stepped in, so key by what was really used
            cache_key = f"{spacy_model_name()}:{file_hash}" if file_hash else None
            store_candidates(cache_key, mentions, cursor, conn)
        elif progress_callback:
            progress_callback(
                {
                    "phase": "reading",
                    "message": borges_says(
                        f"Already read {file_path.name}: reusing his notes"
                    ),
                    "agent": "borges",
                }
            )
        mentions = add_reader_mentions(
            mentions, file_path, source_book_id, file_hash, cursor, conn, progress_callback
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
        result = _store_references(
            mentions,
            source_book_id,
            cursor,
            conn,
            quiet=quiet,
            progress_callback=progress_callback,
        )
        if result.get("judged"):
            # Claude judged the mentions, so Claude can also check the finished links
            result["audit"] = audit_book(
                source_book_id, cursor, conn, progress_callback=progress_callback, quiet=quiet
            )
        return result
    except Exception as e:
        if not quiet:
            print(f"Error processing file {file_path}: {e}")
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


def clean_metadata_title(title: str) -> str:
    """Drop series tags such as "(Bobiverse Book 1)" from an EPUB title."""
    title = re.sub(r"\s+", " ", title or "").strip()
    title = re.sub(r"\s*\([^()]*\bbook\s*\d+[^()]*\)\s*$", "", title, flags=re.I)
    return title.strip()


def normalize_author_name(author: str) -> str:
    """Turn "Wynn-Williams, Sarah" into "Sarah Wynn-Williams"."""
    author = re.sub(r"\s+", " ", author or "").strip()
    if author.count(",") == 1:
        last, first = [part.strip() for part in author.split(",")]
        if first and last and len(first.split()) <= 3:
            return f"{first} {last}"
    return author


def read_epub_metadata(file_path: Path) -> tuple:
    """(title, author) from the EPUB's own metadata; None for a missing field."""
    if epub is None:
        return None, None
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            book = epub.read_epub(str(file_path))
        titles = [t[0] for t in book.get_metadata("DC", "title") if t and t[0]]
        creators = [c[0] for c in book.get_metadata("DC", "creator") if c and c[0]]
    except Exception:
        return None, None
    title = clean_metadata_title(titles[0]) if titles else None
    author = normalize_author_name(creators[0]) if creators else None
    if title and title.lower() in ("unknown", "untitled"):
        title = None
    if author and author.lower() in ("unknown", "anonymous"):
        author = None
    return title or None, author or None


_IDENTITY_CACHE: Dict[tuple, tuple] = {}


def file_title_and_author(file_path: Path) -> tuple:
    """
    Title and author of a library file. The EPUB's embedded metadata wins;
    the filename is only a fallback (it gave "Wynn-Williams, Sarah" as a title).
    """
    file_path = Path(file_path)
    try:
        stat = file_path.stat()
        cache_key = (str(file_path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return extract_title_and_author(file_path.name)
    if cache_key in _IDENTITY_CACHE:
        return _IDENTITY_CACHE[cache_key]

    name_title, name_author = extract_title_and_author(file_path.name)
    title, author = name_title, name_author
    if file_path.suffix.lower() == ".epub":
        meta_title, meta_author = read_epub_metadata(file_path)
        title = meta_title or name_title
        author = meta_author or name_author
    _IDENTITY_CACHE[cache_key] = (title, author)
    return title, author


def repair_owned_books(cursor, conn, books_dir: Optional[Path] = None) -> int:
    """
    Tie each processed file to its library row and correct rows whose title or
    author came from a misread filename. Local only (no network).
    """
    books_dir = books_dir or Path("books")
    if not books_dir.exists():
        return 0
    repaired = 0
    for file_path in list_book_files(books_dir):
        title, author = file_title_and_author(file_path)
        name_title, _name_author = extract_title_and_author(file_path.name)

        cursor.execute(
            "SELECT book_id FROM processed_files WHERE filename = ?", (file_path.name,)
        )
        processed = cursor.fetchone()
        if not processed:
            continue  # not extracted yet; process_single_file will file it
        book_id = processed[0]
        if book_id is not None:
            cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
            if not cursor.fetchone():
                book_id = None
        if book_id is None:
            for candidate in (title, name_title):
                cursor.execute(
                    "SELECT id FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))",
                    (candidate,),
                )
                found = cursor.fetchone()
                if found:
                    book_id = found[0]
                    break
        if book_id is None:
            book_id = _find_local_book(title, cursor) or _find_local_book(name_title, cursor)
        if book_id is None:
            continue

        cursor.execute(
            "UPDATE processed_files SET book_id = ? WHERE filename = ?",
            (book_id, file_path.name),
        )
        cursor.execute(
            "SELECT title, author, IFNULL(user_edited, 0) FROM books WHERE id = ?", (book_id,)
        )
        row_title, row_author, user_edited = cursor.fetchone()
        if user_edited:
            continue  # a person set these by hand
        changed = False
        if title_similarity(row_title or "", title) < MIN_VERIFY_SCORE:
            cursor.execute(
                "SELECT 1 FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?)) AND id != ?",
                (title, book_id),
            )
            if not cursor.fetchone():
                cursor.execute("UPDATE books SET title = ? WHERE id = ?", (title, book_id))
                changed = True
        if author and author != "Unknown" and not author_matches(author, row_author):
            cursor.execute("UPDATE books SET author = ? WHERE id = ?", (author, book_id))
            changed = True
        repaired += 1 if changed else 0
    conn.commit()
    return repaired


def refresh_owned_record(book_id: int, title: str, author: str, cursor, conn) -> bool:
    """
    Point a library book at the catalogue record for its real title and author.
    The first match was made on the title alone, so Zweig's "Montaigne" was
    filed under Montaigne's own Essais (wrong cover, wrong work).
    Returns True when the record changed.
    """
    if not author or author == "Unknown":
        return False
    cursor.execute("SELECT open_library_id, author FROM books WHERE id = ?", (book_id,))
    row = cursor.fetchone()
    if not row:
        return False
    match = lookup_open_library(title, author=author)
    if not match or match["open_library_id"] == row[0]:
        return False

    # Another row may already be that work (it was mentioned by another book):
    # fold it into the library row so the two become one node.
    cursor.execute(
        "SELECT id FROM books WHERE open_library_id = ? AND id != ?",
        (match["open_library_id"], book_id),
    )
    other = cursor.fetchone()
    if other:
        _remap_book_id(cursor, other[0], book_id)
    cursor.execute(
        """
        UPDATE books
        SET open_library_id = ?, cover_id = ?, cover_url = NULL,
            verified = 1, match_score = ?
        WHERE id = ?
        """,
        (match["open_library_id"], match.get("cover_id"), match["match_score"], book_id),
    )
    conn.commit()
    return True


def refresh_library_records(quiet: bool = False) -> int:
    """Re-resolve every library book by title and author. Needs the network."""
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT b.id, b.title, b.author FROM books b
        JOIN processed_files p ON p.book_id = b.id
        """
    )
    changed = 0
    for book_id, title, author in cursor.fetchall():
        try:
            if refresh_owned_record(book_id, title, author, cursor, conn):
                changed += 1
                if not quiet:
                    print(f"  Re-filed: {title} by {author}")
        except Exception as e:
            if not quiet:
                print(f"  Could not re-file {title}: {e}")
    conn.close()
    return changed


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


def mark_file_processed(
    filename: str, file_hash: str, cursor, conn, book_id: Optional[int] = None
):
    """Mark a file as processed and remember which library row it is."""
    cursor.execute(
        """
        INSERT OR REPLACE INTO processed_files (filename, file_hash, processed_at, book_id)
        VALUES (?, ?, CURRENT_TIMESTAMP, ?)
        """,
        (filename, file_hash, book_id),
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

    # Remember which row this file was before forgetting that it was processed
    cursor.execute("SELECT book_id FROM processed_files WHERE filename = ?", (file_path.name,))
    known = cursor.fetchone()
    known_book_id = known[0] if known else None
    if known_book_id is not None:
        cursor.execute("SELECT 1 FROM books WHERE id = ?", (known_book_id,))
        if not cursor.fetchone():
            known_book_id = None

    if force:
        cursor.execute("DELETE FROM processed_files WHERE filename = ?", (file_path.name,))
        conn.commit()

    title, author = file_title_and_author(file_path)
    if known_book_id is not None:
        cursor.execute(
            "SELECT title, author FROM books WHERE id = ? AND IFNULL(user_edited, 0) = 1",
            (known_book_id,),
        )
        edited = cursor.fetchone()
        if edited:  # hand-set title/author beat the file's metadata
            title, author = edited[0], edited[1] or author
    if progress_callback:
        progress_callback(
            {
                "phase": "starting",
                "message": borges_says(f"Preparing {file_path.name}"),
                "current_file": file_path.name,
                "agent": "borges",
            }
        )
    source_book_id = known_book_id or get_or_create_book(
        title,
        cursor,
        conn,
        require_verified=False,
        author=author if author != "Unknown" else None,
    )
    if source_book_id is None:
        raise RuntimeError(f"Could not create library book for '{title}'")
    if author != "Unknown":
        cursor.execute(
            "UPDATE books SET author = ? WHERE id = ? AND (author IS NULL OR author = 'Unknown')",
            (author, source_book_id),
        )
        conn.commit()
        try:
            refresh_owned_record(source_book_id, title, author, cursor, conn)
        except Exception:
            pass  # offline: keep the record we have

    # Drop old outgoing refs before reprocess so results stay fresh.
    # Links a person confirmed in the review queue are kept.
    if force:
        cursor.execute(
            "DELETE FROM book_references WHERE source_book_id = ? AND IFNULL(confirmed, 0) = 0",
            (source_book_id,),
        )
        conn.commit()

    def file_progress(updates):
        if progress_callback:
            payload = {"current_file": file_path.name}
            payload.update(updates)
            progress_callback(payload)

    suffix = file_path.suffix.lower()
    if suffix not in (".txt", ".epub"):
        raise ValueError(f"Unsupported file type: {suffix}")
    process_book_file(
        file_path,
        source_book_id,
        cursor,
        conn,
        quiet=quiet,
        progress_callback=file_progress,
        file_hash=file_hash,
    )

    if progress_callback:
        progress_callback(
            {
                "phase": "saving",
                "message": lucien_says(f"Filing {file_path.name} on the shelves"),
                "current_file": file_path.name,
                "agent": "lucien",
            }
        )
    mark_file_processed(file_path.name, file_hash, cursor, conn, book_id=source_book_id)
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
            "judging",
            "auditing",
            "reading_closely",
        ):
            print(status.get("message", ""))

    # --force re-reads every book, e.g. after turning the Judge on
    # --fix-library re-files library books under their real title and author
    import sys

    if "--check" in sys.argv[1:]:
        # One small question to Claude, to see that the chosen provider answers
        import judge

        status = judge.judge_status()
        print(f"Provider: {status['provider']}   model: {status['model'] or '-'}")
        if not status["available"]:
            print(f"The Judge is off: {status['reason']}")
            return
        sample = [
            {"index": 0, "title": "Middlemarch", "context": "She was reading Middlemarch again."}
        ]
        try:
            answer = judge._call(judge._get_client(), "A Test Book", "", sample)
        except Exception as e:
            print(f"It did not answer: {type(e).__name__}: {e}")
            return
        print("It answered:", json.dumps(answer, ensure_ascii=False)[:400])
        return
    if "--rescue" in sys.argv[1:]:
        init_database(quiet=True)
        print(f"Rescued {rescue_dropped_works()} dropped work(s); run --audit to check them")
        return
    if "--audit" in sys.argv[1:]:
        init_database(quiet=True)
        totals = audit_library()
        print(
            f"Auditor: {totals['ok']} right, {totals['fixed']} re-filed, "
            f"{totals['removed']} removed, {totals['unsure']} unsure"
        )
        return
    if "--fix-library" in sys.argv[1:]:
        init_database(quiet=True)
        print(f"Re-filed {refresh_library_records()} library book(s)")
        return
    force = "--force" in sys.argv[1:]
    result = process_pending_files(progress_callback=on_progress, quiet=False, force=force)

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
