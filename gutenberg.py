"""
Free texts for the shared catalogue.

A work your books mention is only a dot on the map until its own text is
read. For works in the public domain the text is on Project Gutenberg. This
module finds it (through the Gutendex index) and fetches the plain text, with
Gutenberg's licence pages cut off, so the ordinary pipeline can read it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional

import requests

from titles import author_matches, title_similarity

INDEX_URL = "https://gutendex.com/books/"
CATALOGUE_DIR = Path("catalogue")
USER_AGENT = "books-in-books (local reading tool)"

_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S)
_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK", re.I)


def strip_licence(text: str) -> str:
    """Keep the book; drop Project Gutenberg's header and licence."""
    start = _START.search(text)
    if start:
        text = text[start.end() :]
    end = _END.search(text)
    if end:
        text = text[: end.start()]
    return text.strip()


def _plain_text_url(formats: Dict[str, str]) -> Optional[str]:
    for kind, url in formats.items():
        if kind.startswith("text/plain") and not url.endswith(".zip"):
            return url
    return None


def find_text(title: str, author: str = "") -> Optional[Dict]:
    """
    The Gutenberg edition of a work, or None. Only an English text whose title
    matches and whose author (when known) agrees is accepted: a wrong text
    would fill the map with another book's mentions.
    Returns {"gutenberg_id", "title", "author", "url"}.
    """
    query = f"{title} {author}".strip()
    response = None
    for attempt in (1, 2, 3):  # the index is a volunteer service and often slow
        try:
            response = requests.get(
                INDEX_URL,
                params={"search": query, "languages": "en"},
                headers={"User-Agent": USER_AGENT},
                timeout=240,
            )
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 3:
                raise
    best, best_score = None, 0.0
    for book in response.json().get("results") or []:
        url = _plain_text_url(book.get("formats") or {})
        if not url:
            continue
        # "Emma" must not fetch "Emma; or, something else entirely": compare the main title
        main_title = re.split(r"\s*[;:]\s+", book.get("title") or "")[0]
        score = max(title_similarity(title, book.get("title") or ""), title_similarity(title, main_title))
        if score < 0.9:
            continue
        names = [a.get("name") or "" for a in book.get("authors") or []]
        if author and names and not author_matches(author, names):
            continue
        if score > best_score:
            best, best_score = (
                {
                    "gutenberg_id": book["id"],
                    "title": book.get("title"),
                    "author": "; ".join(names),
                    "url": url,
                },
                score,
            )
    return best


def fetch_text(found: Dict, folder: Path = CATALOGUE_DIR) -> Path:
    """Download the text once and return the file it is kept in."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"pg{found['gutenberg_id']}.txt"
    if path.exists() and path.stat().st_size > 1000:
        return path
    response = requests.get(found["url"], headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    if "text" not in (response.headers.get("Content-Type") or "text"):
        raise ValueError("Project Gutenberg did not return a text file")
    text = strip_licence(response.content.decode("utf-8", errors="replace"))
    if len(text) < 2000:
        raise ValueError("the text is too short to be the book")
    path.write_text(text, encoding="utf-8")
    return path
