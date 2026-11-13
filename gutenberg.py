"""
Free texts for the shared catalogue.

A work your books mention is only a dot on the map until its own text is
read. For works in the public domain the text is on Project Gutenberg. This
module keeps a local copy of Gutenberg's catalogue (one file, about 20 MB,
refreshed once a month), finds a work in it without asking any server, and
fetches the plain text with Gutenberg's licence pages cut off, so the
ordinary pipeline can read it.
"""

from __future__ import annotations

import csv
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from titles import author_matches, title_similarity, version_key

CATALOG_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
TEXT_URL = "https://www.gutenberg.org/ebooks/{id}.txt.utf-8"
CATALOGUE_DIR = Path("catalogue")
CATALOG_FILE = "pg_catalog.csv"
INDEX_FILE = "pg_index.db"
INDEX_MAX_AGE_DAYS = 30
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


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _index_key(title: str) -> str:
    """
    The key a title is filed and looked up under. Gutenberg writes "Moby Dick;
    Or, The Whale" and "Frankenstein; Or, The Modern Prometheus": the part
    before the alternative title is what people call the book.
    """
    return version_key(re.split(r"\s*[;:]\s+|,\s+or,?\s+", title or "", flags=re.I)[0])


def build_index(csv_path: Path, index_path: Path) -> int:
    """Turn the catalogue file into a small database that can be searched by title."""
    if index_path.exists():
        index_path.unlink()
    conn = sqlite3.connect(index_path)
    conn.execute(
        "CREATE TABLE texts (id INTEGER PRIMARY KEY, title TEXT, authors TEXT, key TEXT)"
    )
    rows = []
    with open(csv_path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Type") != "Text" or "en" not in (row.get("Language") or "").split("; "):
                continue
            title = _one_line(row.get("Title"))
            key = _index_key(title)
            if not key or not (row.get("Text#") or "").isdigit():
                continue
            rows.append((int(row["Text#"]), title, _one_line(row.get("Authors")), key))
    conn.executemany("INSERT OR REPLACE INTO texts VALUES (?, ?, ?, ?)", rows)
    conn.execute("CREATE INDEX texts_key ON texts (key)")
    conn.commit()
    conn.close()
    return len(rows)


def ensure_index(folder: Path = CATALOGUE_DIR, refresh: bool = False) -> Path:
    """
    The local index, downloading Gutenberg's catalogue when there is none or
    it is more than a month old. With no network an old index is still used.
    """
    folder.mkdir(parents=True, exist_ok=True)
    csv_path, index_path = folder / CATALOG_FILE, folder / INDEX_FILE
    age_days = (
        (time.time() - csv_path.stat().st_mtime) / 86400 if csv_path.exists() else float("inf")
    )
    if refresh or age_days > INDEX_MAX_AGE_DAYS:
        try:
            response = requests.get(
                CATALOG_URL, headers={"User-Agent": USER_AGENT}, timeout=300
            )
            response.raise_for_status()
            if len(response.content) < 1_000_000:
                raise ValueError("the catalogue file is too small to be right")
            csv_path.write_bytes(response.content)
            if index_path.exists():
                index_path.unlink()
        except Exception:
            if not csv_path.exists():
                raise
    if not index_path.exists():
        build_index(csv_path, index_path)
    return index_path


def search(title: str, folder: Path = CATALOGUE_DIR) -> List[Dict]:
    """Every English text in the local index whose title comes down to the same work."""
    index_path = ensure_index(folder)
    conn = sqlite3.connect(index_path)
    rows = conn.execute(
        "SELECT id, title, authors FROM texts WHERE key = ? ORDER BY id", (_index_key(title),)
    ).fetchall()
    conn.close()
    return [{"gutenberg_id": i, "title": t, "author": a} for i, t, a in rows]


def find_text(title: str, author: str = "", folder: Path = CATALOGUE_DIR) -> Optional[Dict]:
    """
    The Gutenberg edition of a work, or None. Only a text whose title matches
    and whose author (when known) agrees is accepted: a wrong text would fill
    the map with another book's mentions. Of several editions the oldest
    entry wins; it is usually the plain one.
    Returns {"gutenberg_id", "title", "author", "url"}.
    """
    best, best_score = None, 0.0
    for found in search(title, folder):
        main_title = re.split(r"\s*[;:]\s+|,\s+or,?\s+", found["title"], flags=re.I)[0]
        score = max(title_similarity(title, found["title"]), title_similarity(title, main_title))
        if score < 0.9:
            continue
        names = [n for n in found["author"].split("; ") if n]
        if author and names and not author_matches(author, names):
            continue
        if author and not names:
            continue  # an anonymous entry cannot be told from a namesake
        if score > best_score:
            best, best_score = found, score
    if best is None:
        return None
    return dict(best, url=TEXT_URL.format(id=best["gutenberg_id"]))


def fetch_text(found: Dict, folder: Path = CATALOGUE_DIR) -> Path:
    """Download the text once and return the file it is kept in."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"pg{found['gutenberg_id']}.txt"
    if path.exists() and path.stat().st_size > 1000:
        return path
    response = requests.get(found["url"], headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    if "text" not in (response.headers.get("Content-Type") or "text"):
        raise ValueError("Project Gutenberg did not return a text file")
    text = strip_licence(response.content.decode("utf-8", errors="replace"))
    if len(text) < 2000:
        raise ValueError("the text is too short to be the book")
    path.write_text(text, encoding="utf-8")
    return path
