"""Owner-scoped reads for backend-owned Day 2 resources."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Conversation, Memory


async def owned_conversation(db: AsyncSession, owner_id: str, conversation_id: str, *, lock: bool = False):
    statement = select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    record = await db.scalar(statement)
    if record is None:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND"})
    return record


async def owned_memory(db: AsyncSession, owner_id: str, memory_id: str):
    record = await db.scalar(select(Memory).where(Memory.id == memory_id, Memory.owner_id == owner_id))
    if record is None:
        raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"})
    return record
