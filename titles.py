"""
Comparing titles and authors: the small, pure helpers everything else leans on.
"""

from __future__ import annotations

from typing import Optional
import html
import re


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


# Creator labels that stand in for "not known" and so cannot tell two works apart
_PLACEHOLDER_CREATORS = {"", "traditional", "anonymous", "unknown", "music", "film / tv", "cocktail"}


def version_key(title: str) -> str:
    """
    What a title is a version of: "Star Wars: Episode V – The Empire Strikes
    Back", "Star Trek (film series)" and "The Star Wars" come down to
    "star wars" and "star trek". Brackets, a subtitle and a leading article go.
    """
    text = re.sub(r"\s*[\(\[].*?[\)\]]", "", (title or "").lower())
    text = re.split(r"\s*[:–—]\s+|\s+-\s+", text.strip())[0]
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"^(the|a|an)\s+", "", " ".join(text.split()))
    return text


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


def override_key(raw_title: Optional[str]) -> str:
    """Key a person's correction by the wording found in the text."""
    return normalize_title(raw_title or "") or (raw_title or "").strip().lower()
