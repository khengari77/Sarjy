"""Pure tool-operation identity and replay policy.

A tool operation is claimed before any external effect, then becomes completed,
failed or unknown.  Only completed results are reusable as successful evidence;
unknown outcomes are never replayed.  The database service owns persistence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final

MAX_OPERATION_KEY = 100
UNKNOWN_TOOL_ERRORS: Final = frozenset(
    {
        "WEATHER_UNAVAILABLE",
        "TOOL_DELIVERY_UNKNOWN",
    }
)
FAILED_TOOL_ERRORS: Final = frozenset(
    {
        "LOCATION_NOT_FOUND",
        "AMBIGUOUS_LOCATION",
        "LOCATION_EXCLUDED",
        "CITY_REQUIRED",
    }
)


class ToolOperationState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ToolReplayKind(StrEnum):
    RESULT = "result"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    UNKNOWN = "unknown"


def operation_key(turn_id: str, call_id: str) -> str:
    """Build a bounded, stable key from the model tool-call identity."""
    if not turn_id or not call_id:
        raise ValueError("TOOL_IDENTITY_REQUIRED")
    raw = f"{turn_id}:{call_id}"
    if len(raw) <= MAX_OPERATION_KEY:
        return raw
    return "op:" + hashlib.sha256(raw.encode()).hexdigest()[:64]


def arguments_fingerprint(arguments: Mapping[str, Any]) -> str:
    """Stable fingerprint of the normalized tool arguments."""
    if not isinstance(arguments, Mapping):
        raise ValueError("TOOL_ARGUMENTS_MUST_BE_OBJECT")
    canonical = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def terminal_state(error_code: str) -> ToolOperationState:
    """Map a known tool error to a durable terminal state."""
    if error_code in UNKNOWN_TOOL_ERRORS:
        return ToolOperationState.UNKNOWN
    if error_code in FAILED_TOOL_ERRORS:
        return ToolOperationState.FAILED
    # Unrecognized external errors are delivery-ambiguous, not retryable.
    return ToolOperationState.UNKNOWN


def replay_kind(state: str | ToolOperationState) -> ToolReplayKind:
    try:
        normalized = ToolOperationState(state)
    except ValueError:
        return ToolReplayKind.UNKNOWN
    return {
        ToolOperationState.COMPLETED: ToolReplayKind.RESULT,
        ToolOperationState.PENDING: ToolReplayKind.IN_PROGRESS,
        ToolOperationState.FAILED: ToolReplayKind.FAILED,
        ToolOperationState.UNKNOWN: ToolReplayKind.UNKNOWN,
    }[normalized]


__all__ = [
    "FAILED_TOOL_ERRORS",
    "MAX_OPERATION_KEY",
    "ToolOperationState",
    "ToolReplayKind",
    "UNKNOWN_TOOL_ERRORS",
    "arguments_fingerprint",
    "operation_key",
    "replay_kind",
    "terminal_state",
]
