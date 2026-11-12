"""Pure, conservative development reservations, never invoice totals."""

from datetime import datetime
from zoneinfo import ZoneInfo

OPENAI_DAILY_MICRO_USD = 1_000_000
OPENAI_REQUEST_MICRO_USD = 20_000
ELEVENLABS_DAILY_CHARACTERS = 500


def budget_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    return now.astimezone(ZoneInfo("Africa/Tripoli")).date().isoformat()


def reserve_units(used: int, requested: int, limit: int) -> int:
    if used < 0 or requested <= 0 or limit < 0:
        raise ValueError("Invalid budget units")
    if used + requested > limit:
        raise ValueError("DAILY_BUDGET_EXHAUSTED")
    return used + requested
