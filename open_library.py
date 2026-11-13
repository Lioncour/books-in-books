"""
Lucien's catalogue: looking a work up in Open Library (and Google Books), with a cache.
"""

from __future__ import annotations

from typing import Dict, List, Optional
import json
import requests
import sqlite3
from titles import MIN_VERIFY_SCORE, STRICT_TITLE_SCORE, author_matches, is_plausible_title_query, normalize_title, title_similarity, token_coverage


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
