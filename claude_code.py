#!/usr/bin/env python3
"""
Claude through the Claude Code command-line tool instead of an API key.

With BOOKS_PROVIDER=claude-code the Judge, the Reader and the Auditor ask the
`claude` program on this machine, run without a window (`claude -p`). It uses
whatever login that program has, so the work is drawn from that Claude
subscription rather than billed per token. Log in once by running `claude` in
a terminal and typing /login.

This module offers the two calls the rest of the app makes on an SDK client,
`messages.create(...)` and `messages.stream(...)`, and answers in the same
shape, so nothing else needs to know which provider is in use.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Dict, Optional

TIMEOUT_SECONDS = 900

# What the requests made by this process have used so far, as the program reports it
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def usage() -> Dict:
    """A copy of the running totals; subtract two of these to price a book."""
    return dict(USAGE)


def _count(reply: Dict):
    used = reply.get("usage") or {}
    USAGE["calls"] += 1
    USAGE["input_tokens"] += sum(
        int(used.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    USAGE["output_tokens"] += int(used.get("output_tokens") or 0)


def executable() -> Optional[str]:
    """Where the `claude` program is, or None when it is not installed."""
    return os.environ.get("BOOKS_CLAUDE_CODE_PATH") or shutil.which("claude")


def _own_environment(environ) -> Dict[str, str]:
    """
    When the app is started from inside a Claude Code session (its terminal, or
    the desktop app), that session's own settings are in the environment and a
    `claude` started from here would try to borrow its login and fail. Drop
    them, so the program uses the login it was given with /login.
    """
    # An API key in the environment would make the program bill that key (or
    # fail on a placeholder) instead of using the subscription login
    environ = {
        k: v for k, v in environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
    if not environ.get("CLAUDECODE"):
        return environ
    return {
        k: v
        for k, v in environ.items()
        if not (
            k == "CLAUDECODE"
            or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_"))
            or k == "ANTHROPIC_BASE_URL"
        )
    }


def _system_text(system) -> str:
    if isinstance(system, str):
        return system
    return "\n\n".join(block.get("text", "") for block in system or [])


def _user_text(messages) -> str:
    parts = []
    for message in messages or []:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(block.get("text", "") for block in content or [])
    return "\n\n".join(parts)


def _answer_json(reply: Dict) -> str:
    """The structured answer as JSON text, wherever this version of the tool put it."""
    structured = reply.get("structured_output")
    if isinstance(structured, (dict, list)):
        return json.dumps(structured, ensure_ascii=False)
    text = (reply.get("result") or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    json.loads(text)  # raises if the tool did not answer in JSON
    return text


def ask(model: str, system, messages, output_config=None, **_ignored):
    program = executable()
    if not program:
        raise RuntimeError("the `claude` program was not found on this machine")
    command = [
        program,
        "-p",
        "--model",
        model,
        "--system-prompt",
        _system_text(system),
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    schema = ((output_config or {}).get("format") or {}).get("schema")
    if schema:
        command += ["--json-schema", json.dumps(schema)]
    effort = (output_config or {}).get("effort")
    if effort:
        command += ["--effort", effort]

    # Run from an empty folder so no project instructions or settings leak in,
    # and without the markers of a surrounding Claude Code session.
    env = _own_environment(os.environ)
    with tempfile.TemporaryDirectory() as folder:
        done = subprocess.run(
            command,
            input=_user_text(messages),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=folder,
            env=env,
            timeout=TIMEOUT_SECONDS,
        )
    try:
        reply = json.loads(done.stdout)
    except ValueError:
        detail = (done.stderr or done.stdout or "").strip()[:300]
        raise RuntimeError(f"Claude Code gave no usable answer (exit {done.returncode}): {detail}")
    if isinstance(reply, list):  # some versions print every event; the result is last
        reply = next((r for r in reversed(reply) if r.get("type") == "result"), {})
    _count(reply)
    if reply.get("is_error"):
        message = str(reply.get("result") or "unknown error")
        if "authenticate" in message.lower() or "oauth" in message.lower():
            message += " (run `claude` in a terminal and type /login)"
        raise RuntimeError(f"Claude Code: {message}")
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=_answer_json(reply))],
        stop_reason="end_turn",
    )


class _Stream:
    def __init__(self, kwargs):
        self._kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return ask(**self._kwargs)


class _Messages:
    def create(self, **kwargs):
        return ask(**kwargs)

    def stream(self, **kwargs):
        return _Stream(kwargs)


class ClaudeCodeClient:
    """Stands in for an SDK client; see the module docstring."""

    def __init__(self):
        self.messages = _Messages()
