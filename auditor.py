#!/usr/bin/env python3
"""
The Auditor: Claude checks the finished links, so a person does not have to.

The Judge rules on a candidate before the catalogue is searched. It never sees
what the search came back with, and that is where the remaining mistakes are:
a study *about* a work filed as the work, an anonymous text credited to an
editor, a quiz show matched to a foreign remake. The Auditor reads each link
the way a reviewer would on /review: the passage, the wording found, and the
record it was filed under. It then

  - marks the link checked when the record is the work the passage means,
  - re-files it when it names the right work instead,
  - removes it when the passage does not refer to a work at all,
  - leaves it flagged when it cannot tell.

Its removals and re-filings are remembered like a person's corrections, so a
re-read does not undo them. A person's own decisions on /review always win,
and anything the Auditor removed can be brought back from "Dropped mentions".
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from judge import _get_client, message_api, resolve_model

BATCH_SIZE = 25
VERDICTS = ("right", "wrong_work", "not_a_reference", "unsure")
KINDS = ("book", "movie", "music", "cocktail")

SYSTEM_PROMPT = """You are checking finished links in a map of which books mention which other works.

Each link was made in two steps. First a passage in a book was read and a work \
was recognised in it. Then a catalogue was searched and the link was filed \
under one of its records. Either step can go wrong. For each link you get the \
passage, the wording found in it, and the record the link is filed under. \
Decide whether that record is the work the passage refers to.

Verdicts:
- "right": the record is the work the passage means. A different edition or \
translation of the same work is still right, and so is a fuller or shorter \
form of its title.
- "wrong_work": the passage does refer to a real work, but the record is a \
different one. Typical cases: a same-titled work by another author; a study, \
guide, biography or dictionary about the work rather than the work; an \
adaptation in another medium than the passage means; a performer or a \
foreign remake standing in for the original; an editor or translator credited \
as the author of an old or anonymous text. Give the right work in \
correct_title, correct_author and correct_kind.
- "not_a_reference": the passage does not refer to a specific real work at \
all (a greeting, a person, a place, a heading, an invented title, the source \
book itself).
- "unsure": you cannot tell from the passage and the record.

correct_author is the author, composer or director; empty when the work is \
anonymous or traditional, or when you are not confident. For any verdict but \
"wrong_work", leave the three correct_ fields as empty strings and "book".

Records marked "filed on the Judge's word" have no catalogue entry behind \
them: check that such a work really exists under that title and creator.

reason is a few words for the person who may look at this later.

Most links are right. Do not change one because you would have phrased the \
title differently; change it only when it points at the wrong work."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "correct_title": {"type": "string"},
                    "correct_author": {"type": "string"},
                    "correct_kind": {"type": "string", "enum": list(KINDS)},
                    "reason": {"type": "string"},
                },
                "required": [
                    "index",
                    "verdict",
                    "correct_title",
                    "correct_author",
                    "correct_kind",
                    "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def auditor_enabled() -> bool:
    return os.environ.get("BOOKS_AUDITOR", "").lower() not in ("0", "off", "false", "no")


def auditor_model() -> str:
    return resolve_model(os.environ.get("BOOKS_AUDITOR_MODEL") or os.environ.get("BOOKS_JUDGE_MODEL"))


def _describe(link: Dict) -> str:
    filed = "filed on the Judge's word, no catalogue entry" if link.get("judge_filed") else "from a catalogue"
    lines = [
        f'[{link["index"]}] passage: …{(link.get("context") or "(none stored)").strip()}…',
        f'    wording found: "{link.get("raw_title") or link["title"]}"',
        f'    filed under: "{link["title"]}" by {link.get("author") or "unknown"} '
        f'({link.get("kind") or "book"}; {filed})',
    ]
    return "\n".join(lines)


def _call(client, source_title: str, source_author: str, batch: List[Dict]) -> Dict:
    by = f" by {source_author}" if source_author and source_author != "Unknown" else ""
    api, extra = message_api(client)
    response = api.create(
        model=auditor_model(),
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        **extra,
        messages=[
            {
                "role": "user",
                "content": f"Source book: {source_title or 'Unknown'}{by}\n\nLinks:\n"
                + "\n".join(_describe(link) for link in batch),
            }
        ],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Auditor stopped early ({response.stop_reason})")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


def audit_links(
    source_title: str,
    source_author: str,
    links: List[Dict],
    progress_callback=None,
    client=None,
) -> Optional[Dict[int, Dict]]:
    """
    links: [{"title", "author", "kind", "raw_title", "context", "judge_filed"}, ...]
    Returns {position in `links`: verdict dict}, or None when the Auditor is
    unavailable. Links it did not answer for are absent and stay unaudited.
    """
    if not links:
        return {}
    client = client or _get_client()
    if client is None:
        return None

    indexed = [dict(link, index=i) for i, link in enumerate(links)]
    batches = [indexed[i : i + BATCH_SIZE] for i in range(0, len(indexed), BATCH_SIZE)]
    verdicts: Dict[int, Dict] = {}
    for n, batch in enumerate(batches, 1):
        if progress_callback:
            progress_callback(
                {
                    "phase": "auditing",
                    "message": f"The Auditor: checking links {n}/{len(batches)}…",
                    "agent": "auditor",
                }
            )
        try:
            result = _call(client, source_title, source_author, batch)
        except Exception as e:
            if n == 1 and not verdicts:
                print(f"The Auditor is unavailable for this book: {type(e).__name__}: {e}")
                return None
            print(f"The Auditor skipped a batch: {type(e).__name__}: {e}")
            continue
        valid = {link["index"] for link in batch}
        for verdict in result.get("verdicts") or []:
            if verdict.get("index") in valid and verdict.get("verdict") in VERDICTS:
                verdicts[verdict["index"]] = verdict
    return verdicts
