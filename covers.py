"""
Finding a cover picture for a work.
"""

from __future__ import annotations

from open_library import lookup_open_library
from typing import Optional
import re
import requests


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
