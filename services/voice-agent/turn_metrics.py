"""Pure turn-metric conversion and collection for the voice worker."""

import math
from collections.abc import Iterable, Mapping
from typing import TypedDict

MAX_METRIC_MS = 120_000

STAGE_FIELDS = (
    "turn_start_ms",
    "command_ms",
    "llm_reserve_ms",
    "tts_reserve_ms",
    "turn_start_server_ms",
    "turn_start_db_ms",
    "command_server_ms",
    "command_db_ms",
    "model_first_chunk_ms",
    "release_wait_ms",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "request_bytes",
    "provider_connect_ms",
    "provider_headers_ms",
    "loop_lag_ms",
)


class TurnStages(TypedDict, total=False):
    turn_start_ms: int | None
    command_ms: int | None
    llm_reserve_ms: int | None
    tts_reserve_ms: int | None
    turn_start_server_ms: int | None
    turn_start_db_ms: int | None
    command_server_ms: int | None
    command_db_ms: int | None
    model_first_chunk_ms: int | None
    release_wait_ms: int | None
    prompt_cache_hit_tokens: int | None
    prompt_cache_miss_tokens: int | None
    request_bytes: int | None
    provider_connect_ms: int | None
    provider_headers_ms: int | None
    loop_lag_ms: int | None


class TurnMetricsPayload(TypedDict):
    turn_id: str
    stt_ms: int | None
    eot_ms: int | None
    llm_ttft_ms: int | None
    tts_ttfb_ms: int | None
    ttfa_ms: int | None
    stages: dict[str, int]


def _seconds_to_ms(value: object) -> int | None:
    """Convert an SDK second value to whole milliseconds, or drop it as unmeasured."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    milliseconds = round(value * 1000)
    if milliseconds > MAX_METRIC_MS:
        return None
    return milliseconds


def elapsed_ms(started: float, ended: float) -> int | None:
    return _seconds_to_ms(ended - started)


def metric_ms(metrics: Mapping[str, object], key: str) -> int | None:
    return _seconds_to_ms(metrics.get(key))


def assistant_metrics(chat_items: Iterable[object]) -> Mapping[str, object]:
    """Return the first measured assistant message of a turn: first audio is what the user waits for."""
    for item in chat_items:
        if getattr(item, "role", None) != "assistant":
            continue
        metrics = getattr(item, "metrics", None)
        if isinstance(metrics, Mapping) and metrics:
            return metrics
    return {}


def build_payload(
    turn_id: str,
    *,
    user_metrics: Mapping[str, object] | None,
    assistant_metrics: Mapping[str, object] | None,
    stages: Mapping[str, int | None],
) -> TurnMetricsPayload:
    user = user_metrics or {}
    assistant = assistant_metrics or {}
    return {
        "turn_id": turn_id,
        "stt_ms": metric_ms(user, "transcription_delay"),
        "eot_ms": metric_ms(user, "end_of_turn_delay"),
        "llm_ttft_ms": metric_ms(assistant, "llm_node_ttft"),
        "tts_ttfb_ms": metric_ms(assistant, "tts_node_ttfb"),
        "ttfa_ms": metric_ms(assistant, "e2e_latency"),
        "stages": {field: value for field in STAGE_FIELDS if (value := stages.get(field)) is not None},
    }
