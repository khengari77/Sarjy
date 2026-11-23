"""Durable accounts, revocable sessions and attempt limits.

Only digests are stored: a session token's SHA-256, and keyed digests of the
address and network source used to count attempts.
"""

import hashlib
import hmac
import secrets
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.accounts import (
    ATTEMPT_RETENTION,
    SESSION_TTL,
    AttemptLimit,
    retry_after_seconds,
    valid_session_token,
    window_start,
)
from app.infrastructure.database import Account, AccountAttemptWindow, AccountSession, Identity
from app.infrastructure.upsert import insert_ignore

# Identities of accounts are reached only through account sessions: their own
# token is discarded at creation, and this date outlives any session.
IDENTITY_EXPIRY = "9999-12-31T00:00:00+00:00"


class AccountSessionRequired(Exception):
    """No live account session accompanies the request."""


class AccountAttemptLimit(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("ACCOUNT_ATTEMPT_LIMIT")
        self.retry_after = retry_after


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def attempt_key(secret: str, limit: AttemptLimit, value: str) -> str:
    return hmac.new(secret.encode(), f"{limit.name}:{value}".encode(), hashlib.sha256).hexdigest()


async def record_attempt(db: AsyncSession, key: str, limit: AttemptLimit, now: datetime) -> None:
    """Count one attempt durably before any provider call; raise when the window is full."""
    start = window_start(now, limit.window).isoformat()
    await db.execute(
        delete(AccountAttemptWindow).where(AccountAttemptWindow.window < (now - ATTEMPT_RETENTION).isoformat())
    )
    await insert_ignore(db, AccountAttemptWindow, {"key": key, "window": start, "attempts": 0})
    counted = await db.execute(
        update(AccountAttemptWindow)
        .where(
            AccountAttemptWindow.key == key,
            AccountAttemptWindow.window == start,
            AccountAttemptWindow.attempts < limit.attempts,
        )
        .values(attempts=AccountAttemptWindow.attempts + 1)
    )
    await db.commit()
    if counted.rowcount != 1:
        raise AccountAttemptLimit(retry_after_seconds(now, limit.window))


async def open_session(
    db: AsyncSession, *, auth_user_id: str, previous_token: str, now: datetime
) -> tuple[Identity, str]:
    """Map a verified provider user to its account and start a session, replacing the caller's old one."""
    account = await db.scalar(select(Account).where(Account.auth_user_id == auth_user_id))
    if account is None:
        account = await _create_account(db, auth_user_id, now)
    if account.disabled_at is not None:
        await db.rollback()
        raise AccountSessionRequired
    token = secrets.token_urlsafe(32)
    db.add(
        AccountSession(
            token_hash=token_digest(token),
            account_id=account.id,
            created_at=now.isoformat(),
            expires_at=(now + SESSION_TTL).isoformat(),
        )
    )
    if valid_session_token(previous_token):
        await _revoke(db, previous_token, now)
    identity = await db.get(Identity, account.identity_id)
    assert identity is not None
    await db.commit()
    return identity, token


async def _create_account(db: AsyncSession, auth_user_id: str, now: datetime) -> Account:
    identity = Identity(id=str(uuid4()), token_hash=token_digest(secrets.token_urlsafe(32)), expires_at=IDENTITY_EXPIRY)
    db.add(identity)
    await db.flush()
    created = await insert_ignore(
        db,
        Account,
        {"id": str(uuid4()), "auth_user_id": auth_user_id, "identity_id": identity.id, "created_at": now.isoformat()},
        index_elements=["auth_user_id"],
    )
    if created.rowcount != 1:
        # A concurrent first sign-in won; its identity is the account's.
        await db.delete(identity)
        await db.flush()
    account = await db.scalar(select(Account).where(Account.auth_user_id == auth_user_id))
    assert account is not None
    return account


async def authorize_account(db: AsyncSession, token: str, now: datetime) -> Identity:
    if not valid_session_token(token):
        raise AccountSessionRequired
    identity = await db.scalar(
        select(Identity)
        .join(Account, Account.identity_id == Identity.id)
        .join(AccountSession, AccountSession.account_id == Account.id)
        .where(
            AccountSession.token_hash == token_digest(token),
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now.isoformat(),
            Account.disabled_at.is_(None),
        )
    )
    if identity is None:
        raise AccountSessionRequired
    return identity


async def close_session(db: AsyncSession, token: str, now: datetime) -> None:
    if valid_session_token(token):
        await _revoke(db, token, now)
        await db.commit()


async def _revoke(db: AsyncSession, token: str, now: datetime) -> None:
    await db.execute(
        update(AccountSession)
        .where(AccountSession.token_hash == token_digest(token), AccountSession.revoked_at.is_(None))
        .values(revoked_at=now.isoformat())
    )
