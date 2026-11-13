"""Serialized provider-request size, shared by admission and the worker.

The worker sends only ``messages``, ``tool_history`` and ``tools``. Admission
must measure that same UTF-8 envelope, not character counts, or it will admit a
turn the worker later refuses. The reserve covers one tool schema and one
continuation round added after ``/internal/agent/turn/start`` has returned.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.budget import DEEPSEEK_MAX_REQUEST_BYTES

# Measured weather schema is ~454 bytes; keep headroom for separators and small
# schema edits. A CI test pins the real schema below this limit.
TOOL_SCHEMA_RESERVE_BYTES = 512
# Assistant text, tool call and tool result item envelopes. Assistant text is
# charged separately as one output-token budget (one byte per token here).
CONTINUATION_ENVELOPE_BYTES = 1024


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


def admission_reserve_bytes(max_output_tokens: int) -> int:
    """Bytes held back for the tool schema and one post-admission continuation."""
    if max_output_tokens < 0:
        raise ValueError("max_output_tokens must be non-negative")
    return TOOL_SCHEMA_RESERVE_BYTES + CONTINUATION_ENVELOPE_BYTES + max_output_tokens


__all__ = [
    "CONTINUATION_ENVELOPE_BYTES",
    "DEEPSEEK_MAX_REQUEST_BYTES",
    "ProviderRequestTooLarge",
    "TOOL_SCHEMA_RESERVE_BYTES",
    "admission_reserve_bytes",
    "provider_request_bytes",
]
