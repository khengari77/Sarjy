"""Predict DeepSeek prompt-cache hits from the previous request's shared prefix.

DeepSeek caches exact prompt prefixes in 64-token blocks. A character budget of
256 approximates a block at roughly four characters per token; aligning the
common prefix to that budget is a conservative estimate of what is served from
cache.
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any

PREFIX_BLOCK_CHARS = 256


def common_prefix_chars(previous: str, current: str) -> int:
    """Return the number of leading characters both strings share.

    Compares whole slices in C rather than looping per character and narrows
    the boundary with a binary search, which keeps large prompts cheap.
    """
    limit = min(len(previous), len(current))
    if previous[:limit] == current[:limit]:
        return limit
    low, high = 0, limit
    while low < high:
        middle = (low + high + 1) // 2
        if previous[:middle] == current[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


def predicted_prefix_hit_pct(previous: str | None, current: str) -> int | None:
    """Estimate the percentage of ``current`` served by a prefix cache.

    ``None`` means there is no comparable previous request or nothing to hit.
    """
    if previous is None or not current:
        return None
    common = common_prefix_chars(previous, current)
    aligned = (common // PREFIX_BLOCK_CHARS) * PREFIX_BLOCK_CHARS
    return min(100, aligned * 100 // len(current))


def canonical_prompt(request: Any, tools: Iterable[Any]) -> str:
    """Serialize the tools and messages that determine a cached prefix.

    It measures the prompt a turn STARTS with: ``tool_history`` (the call/result rounds appended within a
    turn) is left out on purpose, and the prediction is recorded for the turn's first attempt only.
    """
    return json.dumps(
        {
            "tools": [_tool_document(tool) for tool in tools],
            # Only the messages: the rest of the request (selection, memories with embeddings) is large.
            "messages": request.model_dump(mode="json", include={"messages"})["messages"],
        },
        sort_keys=False,
        ensure_ascii=False,
    )


def _tool_document(tool: Any) -> Any:
    dump = getattr(tool, "model_dump", None)
    if dump is not None:
        return dump(mode="json")
    if isinstance(tool, Mapping):
        return dict(tool)
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", ""),
        "parameters": getattr(tool, "parameters", {}),
    }
