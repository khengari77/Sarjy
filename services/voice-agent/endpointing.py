"""Build optional turn-endpointing options from worker environment values."""

from collections.abc import Mapping
from typing import TypedDict

MIN_DELAY_KEY = "ENDPOINTING_MIN_DELAY_SECONDS"
MAX_DELAY_KEY = "ENDPOINTING_MAX_DELAY_SECONDS"

DEFAULT_MIN_DELAY_SECONDS = 0.5
DEFAULT_MAX_DELAY_SECONDS = 3.0
LOWEST_DELAY_SECONDS = 0.05
HIGHEST_DELAY_SECONDS = 6.0


class EndpointingOptions(TypedDict, total=False):
    min_delay: float
    max_delay: float


class TurnHandlingOptions(TypedDict, total=False):
    endpointing: EndpointingOptions


class EndpointingKwargs(TypedDict, total=False):
    turn_handling: TurnHandlingOptions


def endpointing_kwargs(values: Mapping[str, str]) -> EndpointingKwargs:
    """Return the AgentSession keyword arguments for configured endpointing delays.

    An empty mapping when both variables are unset preserves the SDK defaults.
    """
    raw_min = values.get(MIN_DELAY_KEY, "").strip()
    raw_max = values.get(MAX_DELAY_KEY, "").strip()
    if not raw_min and not raw_max:
        return {}

    min_delay = _parse_delay(MIN_DELAY_KEY, raw_min) if raw_min else DEFAULT_MIN_DELAY_SECONDS
    max_delay = _parse_delay(MAX_DELAY_KEY, raw_max) if raw_max else DEFAULT_MAX_DELAY_SECONDS
    if min_delay > max_delay:
        raise ValueError(f"{MIN_DELAY_KEY} ({min_delay}) must not exceed {MAX_DELAY_KEY} ({max_delay})")
    _require_range(MIN_DELAY_KEY, min_delay)
    _require_range(MAX_DELAY_KEY, max_delay)

    endpointing: EndpointingOptions = {}
    if raw_min:
        endpointing["min_delay"] = min_delay
    if raw_max:
        endpointing["max_delay"] = max_delay
    return {"turn_handling": {"endpointing": endpointing}}


def _parse_delay(name: str, raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds, got {raw!r}") from None


def _require_range(name: str, value: float) -> None:
    if not LOWEST_DELAY_SECONDS <= value <= HIGHEST_DELAY_SECONDS:
        raise ValueError(
            f"{name} must be between {LOWEST_DELAY_SECONDS} and {HIGHEST_DELAY_SECONDS} seconds, got {value}"
        )
