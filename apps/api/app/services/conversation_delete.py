"""Tombstone a conversation and erase every stored copy of its text.

Foreign keys to ``conversations.id`` are NOT NULL/NO ACTION, so rows stay; the conversation is marked deleted and its
text is blanked. Temporary (``scope == 'conversation'``) memories are tombstoned like ``delete_memory`` does, while
global facts that merely came from this chat survive.
"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import SCOPE_CONVERSATION
from app.domain.memory_changes import AUTO_SAVE
from app.domain.sessions import call_is_live
from app.infrastructure.database import (
    ContextExclusion,
    ContextOperation,
    ContextSnapshot,
    Conversation,
    Memory,
    MemoryCaptureDecision,
    MemoryWriteRequest,
    ToolCall,
    Turn,
    VoiceSession,
    now,
)
from app.services.authorization import owned_conversation
from app.services.memory_erasure_lock import lock_owner_memories
from app.services.memory_visibility import DELETED, Erased, _lineage, scrub_copies

DELETED_TITLE = "Deleted conversation"


async def _temporary_memories(db: AsyncSession, owner_id: str, conversation_id: str) -> list[Memory]:
    """Temporary facts of this chat, plus the temporary versions they superseded.

    A global fact in the same correction chain is deliberately left alone.
    """
    rows = (
        await db.scalars(
            select(Memory).where(
                Memory.owner_id == owner_id,
                Memory.source_conversation_id == conversation_id,
                Memory.scope == SCOPE_CONVERSATION,
                Memory.status != DELETED,
            )
        )
    ).all()
    collected: dict[str, Memory] = {}
    for row in rows:
        for member in await _lineage(db, owner_id, row):
            if member.owner_id == owner_id and member.scope == SCOPE_CONVERSATION and member.status != DELETED:
                collected.setdefault(member.id, member)
    return list(collected.values())


async def _refuse_active_call(db: AsyncSession, owner_id: str, conversation_id: str) -> None:
    moment = datetime.now(UTC).isoformat()
    sessions = (
        await db.scalars(
            select(VoiceSession).where(
                VoiceSession.conversation_id == conversation_id, VoiceSession.owner_id == owner_id
            )
        )
    ).all()
    if any(call_is_live(session.status, session.expires_at, moment) for session in sessions):
        raise HTTPException(409, detail={"code": "CONVERSATION_ACTIVE"})


async def _surviving(db: AsyncSession, owner_id: str, ids: set) -> set[str]:
    """Which of these memory ids still exist (not tombstoned) for this owner."""
    wanted = [value for value in ids if isinstance(value, str)]
    if not wanted:
        return set()
    rows = await db.scalars(
        select(Memory.id).where(Memory.owner_id == owner_id, Memory.id.in_(wanted), Memory.status != DELETED)
    )
    return set(rows.all())


async def _blank_conversation_text(db: AsyncSession, owner_id: str, conversation_id: str) -> None:
    turns = select(Turn.id).where(Turn.conversation_id == conversation_id)
    await db.execute(delete(ContextSnapshot).where(ContextSnapshot.turn_id.in_(turns)))
    await db.execute(
        delete(ContextExclusion).where(
            ContextExclusion.owner_id == owner_id, ContextExclusion.conversation_id == conversation_id
        )
    )
    await db.execute(update(Turn).where(Turn.conversation_id == conversation_id).values(text="", snapshot=None))
    await db.execute(update(ToolCall).where(ToolCall.turn_id.in_(turns)).values(result=None))
    operations = (
        await db.scalars(
            select(ContextOperation).where(
                ContextOperation.owner_id == owner_id, ContextOperation.conversation_id == conversation_id
            )
        )
    ).all()
    kept = await _surviving(db, owner_id, {(op.payload or {}).get("memory_id") for op in operations})
    for operation in operations:
        payload = operation.payload or {}
        if operation.command_type == AUTO_SAVE and payload.get("memory_id") in kept:
            continue  # the journal entry of a fact that outlives this chat stays, so it can still be undone
        operation.payload = {"kind": payload.get("kind"), "scrubbed": True}


async def delete_conversation(db: AsyncSession, *, owner_id: str, conversation_id: str) -> None:
    """Idempotently tombstone one owned conversation; one transaction, no caller commit."""
    conversation = await owned_conversation(db, owner_id, conversation_id, lock=True, include_deleted=True)
    await lock_owner_memories(db, owner_id)
    if conversation.deleted_at is not None:
        return
    try:
        await _refuse_active_call(db, owner_id, conversation_id)
        temporary = await _temporary_memories(db, owner_id, conversation_id)
        if temporary:
            erased = [Erased.of(row) for row in temporary]
            await db.execute(
                update(Memory)
                .where(Memory.id.in_([row.id for row in temporary]), Memory.owner_id == owner_id)
                .values(status=DELETED, content="", subject=None, embedding=None, updated_at=now())
            )
            await scrub_copies(db, owner_id, erased)
        await _blank_conversation_text(db, owner_id, conversation_id)
        await db.execute(
            delete(MemoryWriteRequest).where(
                MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.conversation_id == conversation_id
            )
        )
        await db.execute(
            delete(MemoryCaptureDecision).where(
                MemoryCaptureDecision.owner_id == owner_id,
                MemoryCaptureDecision.conversation_id == conversation_id,
            )
        )
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.owner_id == owner_id)
            .values(title=DELETED_TITLE, deleted_at=now(), revision=Conversation.revision + 1)
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
