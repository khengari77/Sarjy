"""Pure account policy: input shape, limits and attempt windows."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

SESSION_TTL: Final = timedelta(days=30)
PASSWORD_MIN: Final = 8
# The credential provider hashes at most 72 bytes; longer input would be truncated silently.
PASSWORD_MAX_BYTES: Final = 72
EMAIL_MAX: Final = 254
_EMAIL: Final = re.compile(r"[^\s@]{1,64}@[^\s@.]+(?:\.[^\s@.]+)+")
_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{43}")


@dataclass(frozen=True)
class AttemptLimit:
    """At most ``attempts`` per fixed window."""

    name: str
    attempts: int
    window: timedelta


SOURCE_LIMIT: Final = AttemptLimit("source", 30, timedelta(minutes=10))
# Tight per address and source, loose per address alone: a stranger elsewhere
# must not be able to lock a person out cheaply.
EMAIL_SOURCE_LIMIT: Final = AttemptLimit("email-source", 10, timedelta(minutes=10))
EMAIL_LIMIT: Final = AttemptLimit("email", 60, timedelta(minutes=10))
SIGN_UP_LIMIT: Final = AttemptLimit("sign-up", 10, timedelta(hours=1))
ATTEMPT_RETENTION: Final = timedelta(days=1)


def normalized_email(value: str) -> str | None:
    """The comparison form of an address, or ``None`` when it cannot be one."""
    email = value.strip().lower()
    if len(email) > EMAIL_MAX or _EMAIL.fullmatch(email) is None:
        return None
    return email


def acceptable_password(value: str) -> bool:
    try:
        encoded = value.encode()
    except UnicodeEncodeError:
        return False
    return len(value) >= PASSWORD_MIN and len(encoded) <= PASSWORD_MAX_BYTES and "\x00" not in value


def valid_session_token(value: str) -> bool:
    return _TOKEN.fullmatch(value) is not None


def window_start(now: datetime, window: timedelta) -> datetime:
    seconds = int(window.total_seconds())
    return datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, UTC)


def retry_after_seconds(now: datetime, window: timedelta) -> int:
    remaining = (window_start(now, window) + window - now).total_seconds()
    return max(1, int(remaining) + 1)
