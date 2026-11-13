"""
Working out a book's title and author from its file name and EPUB metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict
import re

try:
    from ebooklib import epub
    import ebooklib
except ImportError:
    epub = None
    ebooklib = None


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
