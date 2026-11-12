#!/usr/bin/env python3
"""
The Reader: Claude reads the book itself and lists the works it mentions.

Borges (spaCy), italics and quotation marks only find titles that look like
titles. A work named in plain prose ("she was reading Middlemarch again", in
roman type) is invisible to all three. The Reader has no such blind spot: it
reads each stretch of text and reports every work referred to, with the
sentence it found it in.

It is off by default because it sends the whole book to the API, which costs
more than judging a few hundred short candidates. Turn it on with

    BOOKS_READER=on

and measure the difference with eval_extraction.py before leaving it on.
Its finds arrive already judged, so they skip the Judge and go straight to the
catalogue lookup.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from judge import MANNERS, _get_client, message_api, resolve_model

DEFAULT_MODEL = "claude-opus-5-5"
CHUNK_CHARS = 40000
# Raise when the prompt changes, so cached readings are not reused
READER_VERSION = 2
KINDS = ("book", "movie", "music", "cocktail")

SYSTEM_PROMPT = """You help build a map of which books mention which other works.

You will be given one stretch of a book. List every specific, real, nameable \
work the text refers to: books, plays, poems, essays, scripture, reference \
works, films, TV series, songs, hymns, pieces of music, and named cocktails.

Include a work when the text names it or unmistakably points at it: a famous \
line quoted from it, a nickname for it ("the Scottish play"), or one of its \
characters used as a name, nickname, picture or figure of speech ("crazy Rambo \
stuff", "spoke like Sherlock Holmes", a Smurf painted on a van, a room named \
after the Wicked Witch of the West). File a character under the work it comes \
from. Leave out: the book you are reading \
itself, its chapter and section headings, newspapers and magazines as \
publications, prizes, brands, games, websites, organisations, people, and \
titles the story invents.

For each work give:
- title: its usual catalogue title, not the abbreviated wording of the text.
- author: the author, composer, best-known performer or director. Empty string \
when the work is anonymous or traditional, or when you are not confident. Never \
attribute a work published after this book.
- kind: "book" for anything written; "movie" for films and TV; "music" for \
songs and music; "cocktail" for mixed drinks.
- manner: "read" when someone reads, owns, buys or is given it; "quoted" when \
its words are quoted or sung; "discussed" when its content or merits are talked \
about; "recommended" when it is praised or urged on someone; "criticised" when \
it is disparaged; "compared" when it is a comparison or figure of speech; \
"passing" for a bare mention.
- quote: the sentence or clause from the text that shows the reference, copied \
exactly, at most 300 characters. It is checked against the text, and a work \
whose quote cannot be found there is discarded.

List each work once per stretch. A wrong entry is worse than a missing one: \
when you are not sure the text means a real work, leave it out. An empty list \
is a fine answer."""

SCHEMA = {
    "type": "object",
    "properties": {
        "works": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "manner": {"type": "string", "enum": list(MANNERS)},
                    "quote": {"type": "string"},
                },
                "required": ["title", "author", "kind", "manner", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["works"],
    "additionalProperties": False,
}


def reader_enabled() -> bool:
    return os.environ.get("BOOKS_READER", "").lower() in ("1", "on", "true", "yes")


def reader_model() -> str:
    return resolve_model(os.environ.get("BOOKS_READER_MODEL"), DEFAULT_MODEL)


def reader_parallel() -> int:
    """How many parts of a book are read at the same time."""
    try:
        return max(1, int(os.environ.get("BOOKS_READER_PARALLEL", "4")))
    except ValueError:
        return 4


def split_into_chunks(text: str, size: int = CHUNK_CHARS) -> List[str]:
    """Cut at paragraph breaks where possible, never in the middle of a word."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut == -1:
                cut = text.rfind(" ", start + size // 2, end)
            if cut > start:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _squash(text: str) -> str:
    """Compare text loosely: case, whitespace and typographic quotes ignored."""
    text = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _grounded(work: Dict, chunk_squashed: str) -> bool:
    """The Reader must show where it saw the work; unsupported entries are dropped."""
    quote = _squash(work.get("quote") or "")
    if len(quote) >= 12 and quote in chunk_squashed:
        return True
    # A slightly misquoted sentence is fine if a long piece of it is really there
    words = quote.split()
    for i in range(0, max(0, len(words) - 5)):
        if " ".join(words[i : i + 6]) in chunk_squashed:
            return True
    return False


def _call(client, source_title: str, source_author: str, chunk: str, part: int, parts: int) -> Dict:
    by = f" by {source_author}" if source_author and source_author != "Unknown" else ""
    api, extra = message_api(client)
    with api.stream(
        model=reader_model(),
        max_tokens=32000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        **extra,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Book: {source_title or 'Unknown'}{by}\n"
                    f"Stretch {part} of {parts}:\n\n{chunk}"
                ),
            }
        ],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Reader stopped early ({response.stop_reason})")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


def read_for_mentions(
    source_title: str,
    source_author: str,
    text: str,
    progress_callback=None,
    client=None,
) -> Optional[List[Dict]]:
    """
    Returns candidate mentions that already carry a verdict:
        {"title", "context", "source": "reader", "verdict": {...}}
    or None when the Reader is unavailable or failed on the first stretch, so
    the caller carries on with the other candidate sources.
    """
    client = client or _get_client()
    if client is None or not text:
        return None

    from concurrent.futures import ThreadPoolExecutor

    chunks = split_into_chunks(text)

    def read_part(n: int):
        try:
            return _call(client, source_title, source_author, chunks[n - 1], n, len(chunks))
        except Exception as e:
            return e

    # A long novel is dozens of parts; reading a few at once turns a quarter
    # of an hour into a few minutes. Results are merged in book order.
    workers = max(1, min(reader_parallel(), len(chunks)))
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, result in zip(range(1, len(chunks) + 1), pool.map(read_part, range(1, len(chunks) + 1))):
            results[n] = result
            done += 1
            if progress_callback:
                progress_callback(
                    {
                        "phase": "reading_closely",
                        "message": f"The Reader: reading closely, part {done}/{len(chunks)}…",
                        "agent": "reader",
                    }
                )

    failures = [n for n, result in results.items() if isinstance(result, Exception)]
    if len(failures) == len(chunks):
        first = results[failures[0]]
        print(f"The Reader is unavailable for this book: {type(first).__name__}: {first}")
        return None
    for n in failures:
        print(f"The Reader skipped part {n}: {type(results[n]).__name__}: {results[n]}")

    mentions: Dict[str, Dict] = {}
    for n, chunk in enumerate(chunks, 1):
        result = results[n]
        if isinstance(result, Exception):
            continue

        squashed = _squash(chunk)
        for work in result.get("works") or []:
            title = (work.get("title") or "").strip()
            if not title or work.get("kind") not in KINDS or not _grounded(work, squashed):
                continue
            key = f'{work["kind"]}::{title.lower()}'
            if key in mentions:
                continue
            mentions[key] = {
                "title": title,
                "context": re.sub(r"\s+", " ", work.get("quote") or "").strip(),
                "source": "reader",
                "verdict": {
                    "is_reference": True,
                    "kind": work["kind"],
                    "canonical_title": title,
                    "author": (work.get("author") or "").strip(),
                    "manner": work.get("manner") if work.get("manner") in MANNERS else "passing",
                    "reason": "found by the Reader",
                },
            }
    return list(mentions.values())
