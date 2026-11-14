"""Wordle voice command classification for the LiveKit worker.

The allowlist is not duplicated here: it is loaded from
``packages/contracts/wordle_commands.json``, the same file the web client
parses, and both sides execute ``wordle_command_fixtures.json`` in their test
suites. The worker only needs to know whether a finalized transcript is a
Wordle command so it can suppress the conversational reply; argument parsing
and spoken results stay in the web client.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SPEC_PATH = Path(__file__).resolve().parents[2] / "packages/contracts/wordle_commands.json"
FIXTURES_PATH = Path(__file__).resolve().parents[2] / "packages/contracts/wordle_command_fixtures.json"
MAX_SAY_CHARS = 300

_SPEC: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
_CONVERSATIONAL = frozenset(str(word) for word in _SPEC["conversational"])
_GUESS_PATTERNS = tuple(re.compile(str(pattern)) for pattern in _SPEC["guess_intent_patterns"])
_SPELLED = re.compile(str(_SPEC["spelled_pattern"]))
_EXACT = {kind: re.compile(str(pattern)) for kind, pattern in _SPEC["exact"].items()}
_AMBIGUOUS_PLAY = re.compile(str(_SPEC["ambiguous_play"]))
_AMBIGUOUS_ACTION = re.compile(str(_SPEC["ambiguous_action"]))


def normalize_utterance(text: str) -> str:
    """Match the web client's normalization exactly."""
    value = text.lower().replace("\u2018", "'").replace("\u2019", "'")
    value = value.replace("\u201c", '"').replace("\u201d", '"')
    value = re.sub(r"[,;]+", " ", value)
    value = re.sub(r"[.!?:]+\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _guess_tail(normalized: str) -> str | None:
    for pattern in _GUESS_PATTERNS:
        match = pattern.match(normalized)
        if match:
            return normalized[match.end() :].strip()
    return None


def _guess_intent(normalized: str) -> str | None:
    tail = _guess_tail(normalized)
    if tail is None:
        return None
    cleaned = re.sub(r"\s+(?:please|for me|now)$", "", tail)
    cleaned = re.sub(r"^(?:the|a|word)\s+", "", cleaned).strip()
    if not cleaned:
        return "ambiguous"
    first = re.sub(r"[^a-z]", "", cleaned.split(" ", 1)[0])
    if first in _CONVERSATIONAL:
        return None
    # Any guess intent is a command, even malformed: the client speaks the
    # re-prompt and nothing must reach the conversational LLM.
    return "guess"


def command_kind(text: str, *, reveal_pending: bool = False) -> str | None:
    """Return the recognized command kind, or ``None`` for ordinary speech."""
    normalized = normalize_utterance(text)
    if not normalized:
        return None

    if reveal_pending:
        if _EXACT["confirm"].match(normalized):
            return "confirm"
        if _EXACT["cancel"].match(normalized):
            return "cancel"

    guess = _guess_intent(normalized)
    if guess:
        return guess

    spellable = re.sub(r"\s+(?:please|for me)$", "", normalized).strip()
    if _SPELLED.match(spellable):
        return "guess"

    for kind in ("play", "remaining", "guesses", "quit", "reveal"):
        if _EXACT[kind].match(normalized):
            return kind

    if _AMBIGUOUS_PLAY.match(normalized) or _AMBIGUOUS_ACTION.match(normalized):
        return "ambiguous"
    return None


def is_wordle_command(text: str, *, reveal_pending: bool = False) -> bool:
    return command_kind(text, reveal_pending=reveal_pending) is not None


def parse_say_payload(payload: str) -> str:
    """Validate an authenticated ``sarjy.say`` payload and return its text."""
    try:
        value: Any = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("SARJY_SAY_PAYLOAD_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("SARJY_SAY_PAYLOAD_INVALID")
    text = value.get("text")
    if not isinstance(text, str):
        raise ValueError("SARJY_SAY_PAYLOAD_INVALID")
    cleaned = text.strip()
    if not cleaned or len(cleaned) > MAX_SAY_CHARS or any(ord(char) < 32 for char in cleaned):
        raise ValueError("SARJY_SAY_PAYLOAD_INVALID")
    return cleaned
