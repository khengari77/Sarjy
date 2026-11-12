"""Pure, conservative development reservations, never invoice totals."""
from datetime import datetime
from zoneinfo import ZoneInfo

OPENAI_DAILY_MICRO_USD = 1_000_000
OPENAI_REQUEST_MICRO_USD = 20_000
ELEVENLABS_DAILY_CHARACTERS = 500
DEEPSEEK_DAILY_MICRO_USD = 250_000
# Peak-price bound derived from 16,384 input tokens plus the tier-5 output cap.
DEEPSEEK_MAX_INPUT_TOKENS = 16_384
DEEPSEEK_MAX_OUTPUT_TOKENS = 1_024
DEEPSEEK_PEAK_INPUT_MICRO_USD_PER_MILLION = 350_000
DEEPSEEK_PEAK_OUTPUT_MICRO_USD_PER_MILLION = 400_000


def deepseek_reservation_micro_usd(input_tokens: int = DEEPSEEK_MAX_INPUT_TOKENS,
        output_tokens: int = DEEPSEEK_MAX_OUTPUT_TOKENS) -> int:
    input_cost = input_tokens * DEEPSEEK_PEAK_INPUT_MICRO_USD_PER_MILLION
    output_cost = output_tokens * DEEPSEEK_PEAK_OUTPUT_MICRO_USD_PER_MILLION
    return (input_cost + output_cost + 999_999) // 1_000_000


DEEPSEEK_MINIMUM_REQUEST_MICRO_USD = deepseek_reservation_micro_usd()
DEEPSEEK_REQUEST_MICRO_USD = 7_000
# Without a provider tokenizer, one token per UTF-8 byte is the safe upper bound.
DEEPSEEK_MAX_REQUEST_BYTES = DEEPSEEK_MAX_INPUT_TOKENS


def budget_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError('An aware timestamp is required')
    return now.astimezone(ZoneInfo('Africa/Tripoli')).date().isoformat()


def reserve_units(used: int, requested: int, limit: int) -> int:
    if used < 0 or requested <= 0 or limit < 0:
        raise ValueError('Invalid budget units')
    if used + requested > limit:
        raise ValueError('DAILY_BUDGET_EXHAUSTED')
    return used + requested
