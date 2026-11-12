"""Shared FastAPI dependencies constructed by the composition root."""

import hashlib
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Conversation, Identity

COOKIE = "sarjy_demo"


def build_dependencies(sessions):
    """Return the request-scoped dependencies bound to one session factory."""

    async def database():
        async with sessions() as db:
            yield db

    async def identity(request: Request, db: AsyncSession = Depends(database)):
        token = request.cookies.get(COOKIE, "")
        user = await db.scalar(
            select(Identity).where(Identity.token_hash == hashlib.sha256(token.encode()).hexdigest())
        )
        if not user or user.expires_at <= datetime.now(UTC).isoformat():
            raise HTTPException(401, detail={"code": "DEMO_SESSION_REQUIRED"})
        return user

    async def owned(conversation_id: str, user: Identity, db: AsyncSession):
        conversation = await db.get(Conversation, conversation_id)
        if not conversation or conversation.owner_id != user.id:
            raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND"})
        return conversation

    return database, identity, owned
