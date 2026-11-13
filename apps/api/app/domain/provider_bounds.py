"""Serialized provider-request size, shared by admission and the worker.

The worker sends only ``messages``, ``tool_history`` and ``tools``. Admission
must measure that same UTF-8 envelope, not character counts, or it will admit a
turn the worker later refuses. The reserve is an explicit policy: worst-case
UTF-8 output bytes for one completion plus a fixed number of tool rounds added
after ``/internal/agent/turn/start`` has returned.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.budget import DEEPSEEK_MAX_REQUEST_BYTES

# A token decodes to more than one UTF-8 byte; use the maximum scalar width.
MAX_UTF8_BYTES_PER_OUTPUT_TOKEN = 4
# Measured weather schema is ~456 bytes; keep headroom for separators and small
# schema edits. A CI test pins the real schema below this limit.
TOOL_SCHEMA_RESERVE_BYTES = 512
# One assistant text/tool-call/tool-result round. Two rounds cover the observed
# two-city weather call; more than that fails closed at the worker guard.
CONTINUATION_TOOL_ROUND_BYTES = 1024
MAX_CONTINUATION_TOOL_ROUNDS = 2


class ProviderRequestTooLarge(ValueError):
    """A fully assembled provider request exceeds the serialized byte bound."""

    code = "PROVIDER_REQUEST_TOO_LARGE"


def provider_request_bytes(
    messages: Sequence[Mapping[str, Any]],
    tool_history: Sequence[Mapping[str, Any]] = (),
    tools: Sequence[Mapping[str, Any]] = (),
) -> int:
    """Return the UTF-8 length of the worker's provider-bound JSON envelope.

    Mirrors ``services/voice-agent/main.py``: only these three keys, default
    separators, ``ensure_ascii=False`` and plain dictionaries. Callers must not
    pass pydantic models or ``MappingProxyType`` values.
    """
    envelope = {
        "messages": [{"role": item["role"], "content": item["content"]} for item in messages],
        "tool_history": [dict(item) for item in tool_history],
        "tools": [dict(item) for item in tools],
    }
    return len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))


def continuation_reserve_bytes(max_output_tokens: int) -> int:
    """Worst-case bytes for one completion plus the bounded tool rounds."""
    if max_output_tokens < 0:
        raise ValueError("max_output_tokens must be non-negative")
    output_bytes = max_output_tokens * MAX_UTF8_BYTES_PER_OUTPUT_TOKEN
    tool_bytes = CONTINUATION_TOOL_ROUND_BYTES * MAX_CONTINUATION_TOOL_ROUNDS
    return output_bytes + tool_bytes


def admission_reserve_bytes(max_output_tokens: int) -> int:
    """Bytes held back for the tool schema and post-admission continuation."""
    return TOOL_SCHEMA_RESERVE_BYTES + continuation_reserve_bytes(max_output_tokens)


__all__ = [
    "CONTINUATION_TOOL_ROUND_BYTES",
    "DEEPSEEK_MAX_REQUEST_BYTES",
    "MAX_CONTINUATION_TOOL_ROUNDS",
    "MAX_UTF8_BYTES_PER_OUTPUT_TOKEN",
    "ProviderRequestTooLarge",
    "TOOL_SCHEMA_RESERVE_BYTES",
    "admission_reserve_bytes",
    "continuation_reserve_bytes",
    "provider_request_bytes",
]
