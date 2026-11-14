"""Shared FastAPI dependencies constructed by the composition root."""

import hashlib
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.infrastructure.database import Identity
from app.services.authorization import owned_conversation
from app.services.reviewer_access import (
    ReviewerAccessDenied,
    ReviewerSessionRequired,
    authorize,
    cleanup_identity,
)

COOKIE = "sarjy_demo"


async def _reviewer_user(db: AsyncSession, token: str, now, *, cleanup: bool) -> Identity:
    try:
        if cleanup:
            return await cleanup_identity(db, token, now)
        return await authorize(db, token, now)
    except ReviewerSessionRequired:
        raise HTTPException(401, detail={"code": "REVIEWER_SESSION_REQUIRED"}) from None
    except ReviewerAccessDenied:
        raise HTTPException(403, detail={"code": "REVIEWER_ACCESS_DENIED"}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail={"code": "REVIEWER_UNAVAILABLE"}) from None


async def _demo_user(db: AsyncSession, token: str) -> Identity:
    user = await db.scalar(select(Identity).where(Identity.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if not user or user.expires_at <= datetime.now(UTC).isoformat():
        raise HTTPException(401, detail={"code": "DEMO_SESSION_REQUIRED"})
    return user


def _database_dependency(sessions):
    async def database():
        async with sessions() as db:
            yield db

    return database


def _identity_dependency(settings: Settings, clock, database):
    async def identity(request: Request, db: AsyncSession = Depends(database)):
        token = request.cookies.get(COOKIE, "")
        if settings.reviewer_access_enabled:
            return await _reviewer_user(db, token, clock(), cleanup=False)
        return await _demo_user(db, token)

    return identity


def _cleanup_dependency(settings: Settings, clock, database):
    async def cleanup_user(request: Request, db: AsyncSession = Depends(database)):
        token = request.cookies.get(COOKIE, "")
        if settings.reviewer_access_enabled:
            return await _reviewer_user(db, token, clock(), cleanup=True)
        return await _demo_user(db, token)

    return cleanup_user


def _owned_dependency(database):
    async def owned(conversation_id: str, user: Identity, db: AsyncSession):
        return await owned_conversation(db, user.id, conversation_id)

    return owned


def build_dependencies(sessions, *, settings: Settings, clock) -> tuple:
    """Return the request-scoped dependencies bound to one session factory."""
    database = _database_dependency(sessions)
    return (
        database,
        _identity_dependency(settings, clock, database),
        _cleanup_dependency(settings, clock, database),
        _owned_dependency(database),
    )
