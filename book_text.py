"""
Reading a book file and finding what the publisher set in italics or quotation marks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import re

try:
    from ebooklib import epub
    import ebooklib
except ImportError:
    epub = None
    ebooklib = None
from titles import clean_text


CONTEXT_CHARS = 160


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
