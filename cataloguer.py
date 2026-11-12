#!/usr/bin/env python3
"""
The Cataloguer: Claude says which works in the graph are parts of others.

Titles alone cannot tell that the Gospel of Matthew is part of the Bible, or
that "A Scandal in Bohemia" is one of the Adventures of Sherlock Holmes. Left
apart, two books that both reach for the Bible do not meet when one of them
quotes it by book. The Cataloguer is shown the whole list of mentioned works
once and names each part together with the whole it belongs to. The part's
links then go to the whole, keeping the passage and the wording that was found.

It only joins a part to a whole that is already in the list; it never invents
a work, and a work it is unsure about stays where it is.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from judge import _get_client, message_api, resolve_model

SYSTEM_PROMPT = """You are tidying a map of which books mention which other works.

You get a numbered list of the works that are mentioned. Some entries belong \
inside another entry in the same list. Name each one ("part") together with \
the entry it belongs in ("whole"):
- a part of a larger work: a book of the Bible (Genesis, Proverbs, a Gospel) \
when the Bible is listed, an essay, story or sermon when the collection it \
was published in is listed, an episode when its series is listed, a song when \
the film or musical it was written for is listed;
- the same work under another name: its title in the original language or in \
translation ("Essais" and "The Essays" by Montaigne), or a longer and a \
shorter form of its title. Take the better-known form as the whole.

Rules:
- Both numbers must come from the list. Never propose a whole that is not listed.
- Only when the part is unmistakably contained in the whole. A sequel, an \
adaptation in another medium, a retelling, a commentary, a parody or a work \
that merely quotes another is not a part of it.
- Two works by one author are not part and whole unless one was published \
inside the other.
- A play, novel or long poem that stands as a book of its own (Hamlet, The \
Waste Land) stays separate even when a "Complete Works" is listed.
- When in doubt, leave the work alone. An empty list is a fine answer.

reason is a few words for the person who may look at this later."""

SCHEMA = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part": {"type": "integer"},
                    "whole": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["part", "whole", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["parts"],
    "additionalProperties": False,
}


def cataloguer_enabled() -> bool:
    return os.environ.get("BOOKS_CATALOGUER", "").lower() not in ("0", "off", "false", "no")


def cataloguer_model() -> str:
    return resolve_model(
        os.environ.get("BOOKS_CATALOGUER_MODEL") or os.environ.get("BOOKS_JUDGE_MODEL")
    )


def find_parts(works: List[Dict], client=None) -> Optional[List[Dict]]:
    """
    works: [{"id", "title", "author", "kind"}, ...]
    Returns [{"part": id, "whole": id, "reason": str}, ...] using the ids given,
    or None when Claude is unavailable or did not answer.
    """
    if len(works) < 2:
        return []
    client = client or _get_client()
    if client is None:
        return None
    lines = [
        f'[{n}] "{w["title"]}" by {w.get("author") or "unknown"} ({w.get("kind") or "book"})'
        for n, w in enumerate(works)
    ]
    api, extra = message_api(client)
    try:
        response = api.create(
            model=cataloguer_model(),
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            **extra,
            messages=[{"role": "user", "content": "Works:\n" + "\n".join(lines)}],
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"stopped early ({response.stop_reason})")
        text = next((b.text for b in response.content if b.type == "text"), "")
        answer = json.loads(text)
    except Exception as e:
        print(f"The Cataloguer is unavailable: {type(e).__name__}: {e}")
        return None

    pairs, seen = [], set()
    for item in answer.get("parts") or []:
        part, whole = item.get("part"), item.get("whole")
        if not (isinstance(part, int) and isinstance(whole, int)):
            continue
        if part == whole or not (0 <= part < len(works)) or not (0 <= whole < len(works)):
            continue
        if part in seen:
            continue  # a part belongs to one whole
        seen.add(part)
        pairs.append(
            {
                "part": works[part]["id"],
                "whole": works[whole]["id"],
                "reason": (item.get("reason") or "").strip(),
            }
        )
    # A whole that is itself named as a part of something would make a chain;
    # keep it simple and drop pairs whose whole is being merged away
    parts = {p["part"] for p in pairs}
    return [p for p in pairs if p["whole"] not in parts]
