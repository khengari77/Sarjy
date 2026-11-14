"""Fenced lease timestamps shared by dispatcher and memory-capture claims.

A lease is a short, renewable claim on one durable operation. A worker may only
apply effects while it still owns the lease; a takeover replaces the lease id so
the previous holder's fenced writes and receipts fail closed.
"""

from datetime import UTC, datetime, timedelta

LEASE_SECONDS = 120
# Longer than the longest fenced effect (the 20s embedding timeout plus a short
# database write), so a live worker is never taken over mid-write. There is no
# poll loop in memory capture: takeover happens only after this lease expires.
# Expiry is decided by parsing the ISO timestamp, never by string comparison.


def lease_expiry(*, seconds: int = LEASE_SECONDS) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def lease_expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.now(UTC)
    except ValueError:
        return True
