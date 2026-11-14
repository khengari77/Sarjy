"""Pure Wordle rules: puzzle dates, answer validation and two-pass scoring.

Nothing here performs I/O.  The answer is never logged or returned by these
helpers; callers decide when a terminal state permits revealing it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WORDLE_LENGTH: Final = 5
MAX_ATTEMPTS: Final = 6
ANSWER_PATTERN: Final = re.compile(r"^[A-Z]{5}$")


class WordleStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    WON = "won"
    LOST = "lost"


class WordleMark(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    GRAY = "gray"


class WordleValidationError(ValueError):
    """Raised when a puzzle date, answer or guess violates the Wordle contract."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def resolve_puzzle_date(instant: datetime, timezone: str) -> date:
    """Return the calendar date of ``instant`` in the configured timezone."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise WordleValidationError("WORDLE_TIMEZONE_INVALID") from exc
    return instant.astimezone(zone).date()


def validate_answer(value: object) -> str:
    """Validate a source answer: exactly five uppercase A-Z letters."""
    if not isinstance(value, str) or ANSWER_PATTERN.fullmatch(value) is None:
        raise WordleValidationError("WORDLE_ANSWER_INVALID")
    return value


def normalize_guess(value: object) -> str:
    """Normalize a player guess to uppercase and validate its shape."""
    if not isinstance(value, str):
        raise WordleValidationError("WORDLE_GUESS_INVALID")
    normalized = value.strip().upper()
    if ANSWER_PATTERN.fullmatch(normalized) is None:
        raise WordleValidationError("WORDLE_GUESS_INVALID")
    return normalized


def score_guess(answer: str, guess: str) -> tuple[WordleMark, ...]:
    """Score one guess with the two-pass duplicate-letter rule.

    Greens are assigned first and removed from the answer pool.  Yellows are
    then assigned left-to-right while each remaining answer letter can be
    consumed at most once, so repeated guess letters never over-match.
    """
    validated_answer = validate_answer(answer)
    normalized_guess = normalize_guess(guess)
    marks: list[WordleMark] = [WordleMark.GRAY] * WORDLE_LENGTH
    remaining: dict[str, int] = {}
    for position, letter in enumerate(validated_answer):
        if normalized_guess[position] == letter:
            marks[position] = WordleMark.GREEN
        else:
            remaining[letter] = remaining.get(letter, 0) + 1
    for position, letter in enumerate(normalized_guess):
        if marks[position] is WordleMark.GREEN:
            continue
        available = remaining.get(letter, 0)
        if available:
            marks[position] = WordleMark.YELLOW
            remaining[letter] = available - 1
    return tuple(marks)


def status_after_guess(answer: str, guess: str, attempt: int) -> WordleStatus:
    """Return the game status after ``attempt`` has been scored."""
    validated_answer = validate_answer(answer)
    normalized_guess = normalize_guess(guess)
    if not 1 <= attempt <= MAX_ATTEMPTS:
        raise WordleValidationError("WORDLE_ATTEMPT_INVALID")
    if normalized_guess == validated_answer:
        return WordleStatus.WON
    if attempt == MAX_ATTEMPTS:
        return WordleStatus.LOST
    return WordleStatus.IN_PROGRESS
