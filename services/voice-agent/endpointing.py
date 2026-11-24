"""Build the AgentSession turn-handling options from worker environment values.

Once ``turn_handling`` is passed, livekit-agents 1.8 ignores every legacy keyword argument
(``preemptive_generation=``, ``min_endpointing_delay=``...). Every turn-handling choice
therefore lives here, in one mapping; passing a legacy keyword next to it is a silent no-op.
"""

from collections.abc import Mapping
from typing import TypedDict

from config import preemptive_generation

MIN_DELAY_KEY = "ENDPOINTING_MIN_DELAY_SECONDS"
MAX_DELAY_KEY = "ENDPOINTING_MAX_DELAY_SECONDS"

LOWEST_DELAY_SECONDS = 0.05
HIGHEST_DELAY_SECONDS = 6.0


class EndpointingOptions(TypedDict, total=False):
    min_delay: float
    max_delay: float


class PreemptiveGenerationOptions(TypedDict, total=False):
    enabled: bool
    preemptive_tts: bool
    max_retries: int
    max_speech_duration: float


PREEMPTIVE_ON: PreemptiveGenerationOptions = {
    "enabled": True,
    "preemptive_tts": False,
    "max_retries": 1,
    "max_speech_duration": 8.0,
}
PREEMPTIVE_OFF: PreemptiveGenerationOptions = {"enabled": False}


class TurnHandlingOptions(TypedDict, total=False):
    endpointing: EndpointingOptions
    preemptive_generation: PreemptiveGenerationOptions


class EndpointingKwargs(TypedDict, total=False):
    turn_handling: TurnHandlingOptions


def endpointing_kwargs(values: Mapping[str, str]) -> EndpointingKwargs:
    """Return the AgentSession ``turn_handling`` keyword argument.

    Preemptive generation stays opt-in: the worker adopts an early reply only when the final
    transcript matches, so a speculative turn cannot create a second user turn per utterance.
    Endpointing delays are added only when configured; unset keeps the SDK defaults.
    """
    turn_handling: TurnHandlingOptions = {
        "preemptive_generation": dict(PREEMPTIVE_ON if preemptive_generation(values) else PREEMPTIVE_OFF)
    }
    raw_min = values.get(MIN_DELAY_KEY, "").strip()
    raw_max = values.get(MAX_DELAY_KEY, "").strip()
    if not raw_min and not raw_max:
        return {"turn_handling": turn_handling}

    if not (raw_min and raw_max):
        # The SDK's own defaults depend on the turn detector in use (0.3 / 2.5 with the streaming
        # detector), so a half-set pair cannot be checked against them: require both.
        raise ValueError(f"Set both {MIN_DELAY_KEY} and {MAX_DELAY_KEY}, or neither")
    min_delay = _parse_delay(MIN_DELAY_KEY, raw_min)
    max_delay = _parse_delay(MAX_DELAY_KEY, raw_max)
    if min_delay > max_delay:
        raise ValueError(f"{MIN_DELAY_KEY} ({min_delay}) must not exceed {MAX_DELAY_KEY} ({max_delay})")
    _require_range(MIN_DELAY_KEY, min_delay)
    _require_range(MAX_DELAY_KEY, max_delay)

    turn_handling["endpointing"] = {"min_delay": min_delay, "max_delay": max_delay}
    return {"turn_handling": turn_handling}


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
