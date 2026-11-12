"""Owner-scoped reads for backend-owned Day 2 resources."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.database import Conversation, Memory, ContextOperation, ContextSnapshot, Turn, UserSetting


async def owned_conversation(db: AsyncSession, owner_id: str, conversation_id: str):
    record = await db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == owner_id))
    if record is None:
        raise HTTPException(404, detail={'code': 'CONVERSATION_NOT_FOUND'})
    return record


async def owned_memory(db: AsyncSession, owner_id: str, memory_id: str):
    record = await db.scalar(select(Memory).where(Memory.id == memory_id, Memory.owner_id == owner_id))
    if record is None:
        raise HTTPException(404, detail={'code': 'MEMORY_NOT_FOUND'})
    return record


async def owned_operation(db: AsyncSession, owner_id: str, operation_id: str, *, conversation_id: str):
    await owned_conversation(db, owner_id, conversation_id)
    record = await db.scalar(select(ContextOperation).where(ContextOperation.id == operation_id, ContextOperation.owner_id == owner_id, ContextOperation.conversation_id == conversation_id))
    if record is None:
        raise HTTPException(404, detail={'code': 'OPERATION_NOT_FOUND'})
    return record


async def owned_snapshot(db: AsyncSession, owner_id: str, turn_id: str):
    record = await db.scalar(select(ContextSnapshot).join(Turn, Turn.id == ContextSnapshot.turn_id).join(Conversation, Conversation.id == Turn.conversation_id).where(ContextSnapshot.turn_id == turn_id, Conversation.owner_id == owner_id))
    if record is None:
        raise HTTPException(404, detail={'code': 'SNAPSHOT_NOT_FOUND'})
    return record
