#!/usr/bin/env python3
"""
The Judge: Claude reads each candidate mention in its sentence and decides
whether it really is a reference to a work, what kind, and who wrote it.

spaCy (Borges) stays the cheap candidate finder. The Judge sits between Borges
and Lucien: Lucien then looks up the canonical title *with the author*, so a
tune called "Pop Goes the Weasel" no longer becomes a James Patterson novel.

Without credentials (ANTHROPIC_API_KEY, or an `ant auth login` profile) the
Judge is simply off and extraction falls back to the older title-only matching.

Claude can also be reached through Amazon Bedrock (BOOKS_PROVIDER=bedrock),
which bills the AWS account behind your AWS profile instead of an Anthropic
API key. See README: "Using Amazon Bedrock".
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

DEFAULT_MODEL = "claude-opus-5-5"
BEDROCK_PREFIX = "anthropic."  # Bedrock's name for the same model


def load_env_file(path: str = ".env") -> None:
    """
    Read KEY=VALUE lines from a local .env file (gitignored) into the
    environment, without overriding anything already set. Lets the provider,
    region and AWS profile live in one place instead of every terminal.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


load_env_file()


def provider() -> str:
    """Which service answers: 'anthropic' (default) or 'bedrock'."""
    value = os.environ.get("BOOKS_PROVIDER", "anthropic").strip().lower()
    return "bedrock" if value in ("bedrock", "aws") else "anthropic"


def bedrock_api() -> str:
    """
    'mantle': Bedrock's Messages-API endpoint (current models, the default).
    'runtime': the older InvokeModel endpoint, for accounts whose models are
               reached through inference profiles such as
               eu.anthropic.claude-opus-4-6-v1.
    """
    value = os.environ.get("BOOKS_BEDROCK_API", "mantle").strip().lower()
    return "runtime" if value in ("runtime", "invoke", "legacy") else "mantle"


def aws_region() -> Optional[str]:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or None


def resolve_model(configured: Optional[str], default: str = DEFAULT_MODEL) -> str:
    """
    The model id to send. A model you configure is used exactly as written;
    only the built-in default is translated to Bedrock's naming.
    """
    if configured:
        return configured
    if provider() == "bedrock" and not default.startswith(BEDROCK_PREFIX):
        return BEDROCK_PREFIX + default
    return default


def message_api(client):
    """
    Where to send requests, and the extra arguments that go with them.

    On the Claude API a refused request is re-run on a fallback model by the
    server. Bedrock does not offer that parameter, so there the plain Messages
    surface is used and a refusal simply falls back to title-only matching.
    """
    if provider() == "bedrock":
        return client.messages, {}
    return client.beta.messages, {
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }
BATCH_SIZE = 30
VALID_KINDS = ("book", "movie", "music", "cocktail", "other")
MANNERS = ("read", "quoted", "discussed", "recommended", "criticised", "compared", "passing")

SYSTEM_PROMPT = """You help build a map of which books mention which other works.

One book has been scanned for candidate titles, each given with the passage it \
was found in. Candidates come from a named-entity tagger, from text the publisher \
set in italics, and from short capitalised phrases in quotation marks. All three \
are noisy: they pick up chapter headings, prizes, newspapers, organisations, \
slogans, fragments of sentences, the book's own front matter, and (from italics \
and quotes) emphasised words, foreign phrases, ship names, character names and \
signatures. Your job is to judge each candidate from its passage.

For every candidate decide:

- is_reference: true only when the passage refers to a specific, real, nameable \
work: a book, play, poem, essay, film, TV series, song, piece of music, or a \
cocktail. False for anything else (section headings like "Introduction", awards, \
newspapers and magazines, brands, games, websites, places, people, military \
units, invented in-story titles, and references to the source book itself).
- kind: "book" for anything written (novels, plays, poems, essays, scripture, \
reference works); "movie" for films and TV; "music" for songs, hymns, carols, \
nursery tunes, albums and classical pieces; "cocktail" for mixed drinks; "other" \
when is_reference is false.
- canonical_title: the work's usual catalogue title, corrected from the passage's \
wording when it is abbreviated or informal (e.g. "Clarendon's Rebellion" -> \
"The History of the Rebellion"). Keep the candidate's wording when unsure.
- author: the creator the passage most plausibly means: the author for books, \
the composer or best-known performer for music, the director for films. Use the \
passage and the source book's period to choose between works that share a title, \
and never name a work published after the source book. Empty string when the \
work is anonymous or traditional, or when you are not confident.
- manner: how the passage uses the work. "read" when someone reads, owns, buys \
or is given it; "quoted" when its words are quoted or sung; "discussed" when its \
content or merits are talked about; "recommended" when it is praised or urged on \
someone; "criticised" when it is disparaged; "compared" when it serves as a \
comparison or figure of speech; "passing" for a bare mention. Use "passing" when \
is_reference is false.
- reason: a few words, shown to the person reviewing the result.

Also give source_year: the year the source book was first published, or null if \
you are not sure. It is used to reject matches that are newer than the book \
mentioning them.

Judge only from what the passage supports. A wrong link is worse than a missing \
one, so when a candidate could be a work but the passage gives no sign that it \
is being referred to as one, mark it false."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "source_year": {"type": ["integer", "null"]},
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "is_reference": {"type": "boolean"},
                    "kind": {"type": "string", "enum": list(VALID_KINDS)},
                    "canonical_title": {"type": "string"},
                    "author": {"type": "string"},
                    "manner": {"type": "string", "enum": list(MANNERS)},
                    "reason": {"type": "string"},
                },
                "required": [
                    "index",
                    "is_reference",
                    "kind",
                    "canonical_title",
                    "author",
                    "manner",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["source_year", "verdicts"],
    "additionalProperties": False,
}

_client = None
_disabled_reason: Optional[str] = None


def judge_model() -> str:
    return resolve_model(os.environ.get("BOOKS_JUDGE_MODEL"))


def _has_credentials() -> bool:
    """
    Whether the SDK has anything to authenticate with. The client constructs
    happily without credentials and only fails on the first request, so check
    up front. Looks at presence only, never at the values.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("ANTHROPIC_FEDERATION_RULE_ID"):
        return True
    for folder in (
        os.environ.get("ANTHROPIC_CONFIG_DIR"),
        os.path.join(os.path.expanduser("~"), ".config", "anthropic"),
    ):
        try:
            if folder and os.path.isdir(folder) and os.listdir(folder):
                return True
        except OSError:
            continue
    return False


def _get_client():
    """Build the SDK client once. Returns None (and records why) when it can't."""
    global _client, _disabled_reason
    if _client is not None:
        return _client
    if _disabled_reason:
        return None
    if os.environ.get("BOOKS_JUDGE", "").lower() in ("0", "off", "false", "no"):
        _disabled_reason = "turned off with BOOKS_JUDGE=off"
        return None
    try:
        import anthropic
    except ImportError:
        _disabled_reason = "the anthropic package is not installed"
        return None
    if provider() == "bedrock":
        region = aws_region()
        if not region:
            _disabled_reason = "BOOKS_PROVIDER=bedrock needs AWS_REGION (for example eu-central-1)"
            return None
        try:
            # Credentials come from the AWS profile / SSO login, never from here
            options = {"aws_region": region}
            if os.environ.get("AWS_PROFILE"):
                options["aws_profile"] = os.environ["AWS_PROFILE"]
            if bedrock_api() == "runtime":
                _client = anthropic.AnthropicBedrock(**options)
            else:
                _client = anthropic.AnthropicBedrockMantle(**options)
        except Exception as e:
            _disabled_reason = (
                f"could not start the Bedrock client ({type(e).__name__}); "
                "install its extras with: pip install anthropic[bedrock]"
            )
            return None
        return _client

    if not _has_credentials():
        _disabled_reason = "no Anthropic credentials (set ANTHROPIC_API_KEY or run `ant auth login`)"
        return None
    try:
        _client = anthropic.Anthropic()
    except Exception as e:
        _disabled_reason = f"could not start the Anthropic client ({type(e).__name__})"
        return None
    return _client


def judge_status() -> Dict:
    """For /api/health and progress messages. Never exposes the key itself."""
    client = _get_client()
    return {
        "available": client is not None,
        "provider": provider(),
        "model": judge_model() if client is not None else None,
        "reason": None if client is not None else _disabled_reason,
    }


def _disable(reason: str):
    global _client, _disabled_reason
    _client = None
    _disabled_reason = reason


def _build_user_message(source_title: str, source_author: str, batch: List[Dict]) -> str:
    lines = [
        f"Source book: {source_title or 'Unknown'}"
        + (f" by {source_author}" if source_author and source_author != "Unknown" else ""),
        "",
        "Candidates:",
    ]
    for item in batch:
        context = (item.get("context") or "").strip() or "(no passage available)"
        lines.append(f'[{item["index"]}] "{item["title"]}"')
        lines.append(f"    passage: …{context}…")
    return "\n".join(lines)


def _call(client, source_title: str, source_author: str, batch: List[Dict]) -> Dict:
    api, extra = message_api(client)
    response = api.create(
        model=judge_model(),
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
        },
        # On the Claude API, a refused batch is re-run on a fallback model
        **extra,
        messages=[
            {
                "role": "user",
                "content": _build_user_message(source_title, source_author, batch),
            }
        ],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Judge stopped early ({response.stop_reason})")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


def judge_mentions(
    source_title: str,
    source_author: str,
    mentions: List[Dict],
    progress_callback=None,
    client=None,
) -> Optional[Dict]:
    """
    Judge candidate mentions.

    mentions: [{"title": ..., "context": ...}, ...]
    Returns {"source_year": int|None, "verdicts": {position: verdict}} keyed by the
    mention's position in `mentions`, or None when the Judge is unavailable (the
    caller then falls back to title-only matching). Candidates the Judge did not
    answer for are simply absent from the dict.
    """
    client = client or _get_client()
    if client is None or not mentions:
        return None

    try:
        import anthropic
    except ImportError:  # only reachable with an injected client in tests
        anthropic = None

    verdicts: Dict[int, Dict] = {}
    years: List[int] = []
    indexed = [
        {"index": i, "title": m.get("title") or "", "context": m.get("context")}
        for i, m in enumerate(mentions)
    ]
    batches = [indexed[i : i + BATCH_SIZE] for i in range(0, len(indexed), BATCH_SIZE)]

    for n, batch in enumerate(batches, 1):
        if progress_callback:
            progress_callback(
                {
                    "phase": "judging",
                    "message": f"The Judge: weighing mentions {n}/{len(batches)}…",
                    "agent": "judge",
                }
            )
        try:
            result = _call(client, source_title, source_author, batch)
        except Exception as e:
            auth_errors = ()
            if anthropic is not None:
                auth_errors = (
                    anthropic.AuthenticationError,
                    anthropic.PermissionDeniedError,
                )
            if auth_errors and isinstance(e, auth_errors):
                _disable(
                    "AWS refused the request: run `aws sso login` for your profile, and check "
                    "that the account has access to this model"
                    if provider() == "bedrock"
                    else "the Anthropic credentials were rejected"
                )
                return None
            if n == 1 and not verdicts:
                # Nothing judged at all: let the caller use the old path.
                print(f"The Judge is unavailable for this book: {type(e).__name__}: {e}")
                return None
            # Later batch failed: keep what we have, the rest goes unjudged.
            print(f"The Judge skipped a batch: {type(e).__name__}: {e}")
            continue

        valid = {item["index"] for item in batch}
        for verdict in result.get("verdicts") or []:
            idx = verdict.get("index")
            if idx in valid and verdict.get("kind") in VALID_KINDS:
                verdicts[idx] = verdict
        year = result.get("source_year")
        if isinstance(year, int) and 0 < year < 2200:
            years.append(year)

    source_year = max(set(years), key=years.count) if years else None
    return {"source_year": source_year, "verdicts": verdicts}
