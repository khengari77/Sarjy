"""Owner-scoped reads for backend-owned Day 2 resources."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Conversation, Memory


async def owned_conversation(
    db: AsyncSession,
    owner_id: str,
    conversation_id: str,
    *,
    lock: bool = False,
    include_deleted: bool = False,
    key_share: bool = False,
):
    statement = select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == owner_id)
    if not include_deleted:
        statement = statement.where(Conversation.deleted_at.is_(None))
    if lock:
        # ``key_share`` is FOR NO KEY UPDATE: it excludes other writers of the row but not foreign-key checks, so a
        # holder cannot deadlock with a session that inserts a child row (a memory) while holding the owner lock.
        statement = statement.with_for_update(key_share=key_share)
    record = await db.scalar(statement)
    if record is None:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND"})
    return record


async def owned_memory(db: AsyncSession, owner_id: str, memory_id: str):
    record = await db.scalar(select(Memory).where(Memory.id == memory_id, Memory.owner_id == owner_id))
    if record is None:
        raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"})
    return record
