"""Durable reviewer-invitation admission, authorization and operator operations.

Tokens are generated here and only their SHA-256 digests are persisted.  The
module owns the short transactions that make admission limits and invitation
revocation authoritative across API processes on SQLite and PostgreSQL.
"""

import hashlib
import math
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import (
    Identity,
    ReviewerAttemptWindow,
    ReviewerCreationWindow,
    ReviewerInvitation,
)
from app.infrastructure.upsert import insert_ignore

TOKEN_BYTES = 32
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
IDENTITY_TTL = timedelta(days=7)
ATTEMPT_LIMIT = 60
CREATION_LIMIT = 30


class ReviewerSessionRequired(Exception):
    """No usable identity cookie is present."""


class ReviewerAccessDenied(Exception):
    """The identity or submitted invitation is not authorized."""


class ReviewerUnavailable(Exception):
    """Authoritative persistence could not be read; fail closed."""


class ReviewerLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("REVIEWER_ADMISSION_LIMIT")
        self.retry_after = retry_after


@dataclass(frozen=True)
class AdmittedIdentity:
    identity: Identity
    created: bool
    cookie_token: str | None = None


def now_utc() -> datetime:
    return datetime.now(UTC)


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def valid_token_shape(token: object) -> bool:
    return isinstance(token, str) and TOKEN_PATTERN.fullmatch(token) is not None


def _minute_key(value: datetime) -> str:
    return utc_iso(value.replace(second=0, microsecond=0))


def _hour_key(value: datetime) -> str:
    return utc_iso(value.replace(minute=0, second=0, microsecond=0))


def _window_end(value: datetime, *, hourly: bool) -> datetime:
    if hourly:
        return value.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return value.replace(second=0, microsecond=0) + timedelta(minutes=1)


def retry_after(value: datetime, *, hourly: bool) -> int:
    return max(1, math.ceil((_window_end(value, hourly=hourly) - value).total_seconds()))


def invitation_active(invitation: ReviewerInvitation, now: datetime) -> bool:
    if invitation.revoked_at is not None:
        return False
    return invitation.expires_at is None or invitation.expires_at > utc_iso(now)


async def _lookup_identity(db: AsyncSession, token: str) -> Identity | None:
    if not token:
        return None
    return await db.scalar(select(Identity).where(Identity.token_hash == token_hash(token)))


async def _active_invitation(db: AsyncSession, invitation_id: str, now: datetime, *, lock: bool) -> bool:
    invitation = await db.get(ReviewerInvitation, invitation_id, with_for_update=lock)
    return invitation is not None and invitation_active(invitation, now)


async def authorize(db: AsyncSession, token: str, now: datetime, *, lock: bool = False) -> Identity:
    """Authorize an identity cookie for reviewer access.

    The invitation row is re-read on every call so revocation and expiry deny
    subsequent reads, actions and new voice calls.
    """
    identity = await _lookup_identity(db, token)
    if identity is None or identity.expires_at <= utc_iso(now):
        raise ReviewerSessionRequired
    if identity.invitation_id is None:
        # Legacy demo identities never authorize reviewer access.
        raise ReviewerSessionRequired
    if not await _active_invitation(db, identity.invitation_id, now, lock=lock):
        raise ReviewerAccessDenied
    return identity


async def cleanup_identity(db: AsyncSession, token: str, now: datetime) -> Identity:
    """Authenticate the narrow end-session exception.

    Invitation state is deliberately not consulted, but the identity itself
    must still be unexpired and reviewer-linked.
    """
    identity = await _lookup_identity(db, token)
    if identity is None or identity.expires_at <= utc_iso(now) or identity.invitation_id is None:
        raise ReviewerSessionRequired
    return identity


async def _claim_attempt(db: AsyncSession, now: datetime) -> None:
    minute = _minute_key(now)
    await insert_ignore(db, ReviewerAttemptWindow, {"minute": minute, "attempts": 0})
    changed = await db.execute(
        update(ReviewerAttemptWindow)
        .where(ReviewerAttemptWindow.minute == minute, ReviewerAttemptWindow.attempts < ATTEMPT_LIMIT)
        .values(attempts=ReviewerAttemptWindow.attempts + 1)
    )
    if changed.rowcount != 1:
        await db.rollback()
        raise ReviewerLimitExceeded(retry_after(now, hourly=False))
    await db.commit()


async def record_attempt(db: AsyncSession, now: datetime) -> None:
    """Count one POST attempt, committing before the invitation is inspected."""
    await _claim_attempt(db, now)


async def _claim_creation(db: AsyncSession, invitation_id: str, now: datetime) -> None:
    hour = _hour_key(now)
    await insert_ignore(
        db,
        ReviewerCreationWindow,
        {"invitation_id": invitation_id, "hour": hour, "created": 0},
        index_elements=["invitation_id", "hour"],
    )
    changed = await db.execute(
        update(ReviewerCreationWindow)
        .where(
            ReviewerCreationWindow.invitation_id == invitation_id,
            ReviewerCreationWindow.hour == hour,
            ReviewerCreationWindow.created < CREATION_LIMIT,
        )
        .values(created=ReviewerCreationWindow.created + 1)
    )
    if changed.rowcount != 1:
        await db.rollback()
        raise ReviewerLimitExceeded(retry_after(now, hourly=True))


async def admit(db: AsyncSession, cookie_token: str, invitation_token: str, now: datetime) -> AdmittedIdentity:
    """Exchange an invitation for a fixed-expiry seven-day reviewer identity.

    A still-valid reviewer cookie preserves its owner.  A different invitation
    never switches that owner, and a revoked identity is never rebound.
    """
    # The submitted credential is validated before any success is claimed.
    invitation = await db.scalar(
        select(ReviewerInvitation)
        .where(ReviewerInvitation.token_hash == token_hash(invitation_token))
        .with_for_update()
    )
    if invitation is None or not invitation_active(invitation, now):
        raise ReviewerAccessDenied

    existing = await _lookup_identity(db, cookie_token)
    if existing is not None and existing.expires_at > utc_iso(now):
        # A valid browser keeps its owner even when another invitation is
        # presented.  A revoked identity is never silently rebound.
        if existing.invitation_id is None:
            raise ReviewerAccessDenied
        if not await _active_invitation(db, existing.invitation_id, now, lock=True):
            raise ReviewerAccessDenied
        return AdmittedIdentity(identity=existing, created=False)

    await _claim_creation(db, invitation.id, now)
    identity_token = new_token()
    identity = Identity(
        id=str(uuid4()),
        token_hash=token_hash(identity_token),
        expires_at=utc_iso(now + IDENTITY_TTL),
        invitation_id=invitation.id,
    )
    db.add(identity)
    await db.commit()
    return AdmittedIdentity(identity=identity, created=True, cookie_token=identity_token)


async def create_invitation(
    db: AsyncSession,
    *,
    expires_at: datetime | None,
    now: datetime,
) -> tuple[ReviewerInvitation, str]:
    """Create one reusable invitation; the raw token is returned only to the CLI."""
    token = new_token()
    invitation = ReviewerInvitation(
        id=str(uuid4()),
        token_hash=token_hash(token),
        created_at=utc_iso(now),
        expires_at=utc_iso(expires_at) if expires_at is not None else None,
    )
    db.add(invitation)
    await db.commit()
    return invitation, token


async def list_invitations(db: AsyncSession) -> list[dict]:
    rows = (await db.scalars(select(ReviewerInvitation).order_by(ReviewerInvitation.created_at))).all()
    return [
        {
            "id": row.id,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "revoked_at": row.revoked_at,
        }
        for row in rows
    ]


async def revoke_invitation(db: AsyncSession, invitation_id: str, now: datetime) -> bool:
    """Idempotently revoke an invitation without touching identities or ledgers."""
    invitation = await db.get(ReviewerInvitation, invitation_id)
    if invitation is None:
        return False
    if invitation.revoked_at is None:
        await db.execute(
            update(ReviewerInvitation)
            .where(ReviewerInvitation.id == invitation_id, ReviewerInvitation.revoked_at.is_(None))
            .values(revoked_at=utc_iso(now))
        )
        await db.commit()
    return True
