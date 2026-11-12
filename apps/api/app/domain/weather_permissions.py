"""Weather permission decisions (D2.8).

Pure policy: an excluded location memory must not authorize a weather request
unless the user explicitly supplied that city in the current turn. The
imperative shell performs the actual boundary call only after this decision.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

LOCATION_EXCLUDED = 'LOCATION_EXCLUDED'


@dataclass(frozen=True)
class LocationEvidence:
    memory_id: str
    content: str


@dataclass(frozen=True)
class WeatherDecision:
    allowed: bool
    city: str
    country: str | None
    source: Literal['explicit', 'memory']
    memory_id: str | None = None
    reason: str | None = None


def mentions(text: str, name: str) -> bool:
    """Case-insensitive whole-word match; safe for accented/Arabic names."""
    # Treat a qualified tool argument as the same city as its unqualified name.
    needle = name.split(',', 1)[0].strip()
    if not needle:
        return False
    return re.search(rf'(?<!\w){re.escape(needle)}(?!\w)', text or '', re.IGNORECASE) is not None


def _match(city: str, evidence: Sequence[LocationEvidence]) -> str | None:
    for item in evidence:
        if mentions(item.content, city):
            return item.memory_id
    return None


def decide_weather_request(
    *,
    city: str,
    country: str | None,
    user_text: str,
    included: Sequence[LocationEvidence] = (),
    excluded: Sequence[LocationEvidence] = (),
) -> WeatherDecision:
    """Allow, or deny, one weather request against the turn's location evidence.

    * an explicit city in the current user turn always works;
    * an included (active, non-excluded) memory authorizes its city;
    * an excluded/inactive memory's city is denied when the model supplies it
      without the user saying it, so a remembered city cannot silently bypass
      an exclusion.
    * if a city appears in both included and excluded sets, the included wins
      (explicit allowance overrides exclusion).
    """
    explicit = mentions(user_text, city)
    included_memory = _match(city, included)
    excluded_memory = _match(city, excluded)
    if explicit:
        return WeatherDecision(True, city, country, 'explicit', included_memory)
    if excluded_memory is not None and included_memory is None:
        return WeatherDecision(False, city, country, 'memory', excluded_memory, LOCATION_EXCLUDED)
    if included_memory is not None:
        return WeatherDecision(True, city, country, 'memory', included_memory)
    return WeatherDecision(False, city, country, 'memory', reason='LOCATION_CONTEXT_REQUIRED')


__all__ = ['LOCATION_EXCLUDED', 'LocationEvidence', 'WeatherDecision', 'decide_weather_request', 'mentions']
