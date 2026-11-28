"""Write, deduplicate, supersede and cap conversation-scoped (temporary) memories.

Temporary memory is the key facts of one conversation, saved automatically with no consent step. It is always on,
regardless of the permanent-memory mode, and a row dies with its conversation. This module is the write path only; a
background producer calls it after a reply (a later change). It never writes a ``ContextOperation`` row.

Safety floor, applied before any write: a credential, an ID/card/phone number, an email or a referential text is
refused. A permanent fact with the same subject or content wins, because permanent memory is the owner's explicit
choice.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import SCOPE_CONVERSATION, SCOPE_GLOBAL
from app.domain.memory_capture import is_referential, resolve_subject
from app.domain.memory_policy import Sensitivity, classify_sensitivity, has_long_number
from app.infrastructure.database import Memory, MemoryWriteRequest
from app.services.authorization import owned_conversation
from app.services.memory import MemoryResult, MemoryService, memory_result
from app.services.memory_visibility import delete_memory

MAX_TEMPORARY_CHARS = 200
MAX_TEMPORARY_PER_CHAT = 30

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_PHONE = re.compile(r"\+?\d[\d\s().-]{5,}\d", re.UNICODE)


class TemporaryFactOutcome(StrEnum):
    WRITTEN = "written"
    REPLACED = "replaced"
    DUPLICATE = "duplicate"
    SKIPPED_PERMANENT = "skipped_permanent"
    REJECTED = "rejected"
    CHAT_GONE = "chat_gone"


@dataclass(frozen=True)
class TemporaryFactResult:
    outcome: TemporaryFactOutcome
    memory: MemoryResult | None = None


class _NoEmbedding:
    """Temporary rows never call OpenAI; a call here is a bug, not a fallback."""

    async def embed(self, db, text):
        raise RuntimeError("temporary memories are lexical only")


def _clean_text(text) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned or len(cleaned) > MAX_TEMPORARY_CHARS or not cleaned.isprintable():
        return None
    return cleaned


def _is_phone_like(text: str) -> bool:
    return any(sum(character.isdigit() for character in match.group()) >= 7 for match in _PHONE.finditer(text))


def _rejected(text: str) -> bool:
    """The credential/ID floor: a fact carrying one of these is never written automatically."""
    if classify_sensitivity(text) is Sensitivity.CREDENTIAL:
        return True
    if has_long_number(text):
        return True
    if _EMAIL.search(text) is not None:
        return True
    if _is_phone_like(text):
        return True
    return is_referential(text)


def _request_key(turn_id: str, text: str) -> str:
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"memtemp:{turn_id}:{digest}"[:100]


async def _active_temporary(db: AsyncSession, *, owner_id: str, conversation_id: str, subject: str | None = None):
    statement = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.status == "active",
        Memory.scope == SCOPE_CONVERSATION,
        Memory.source_conversation_id == conversation_id,
    )
    if subject is not None:
        statement = statement.where(Memory.subject == subject)
    return await db.scalar(statement.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(1))


async def _evict_oldest(service: MemoryService, *, owner_id: str, conversation_id: str, keep_id: str) -> None:
    """Tombstone the oldest active temporary row of the chat, with the same scrub as a permanent delete."""
    async with service.sessions() as db:
        oldest = await db.scalar(
            select(Memory)
            .where(
                Memory.owner_id == owner_id,
                Memory.status == "active",
                Memory.scope == SCOPE_CONVERSATION,
                Memory.source_conversation_id == conversation_id,
                Memory.id != keep_id,
            )
            .order_by(Memory.updated_at.asc(), Memory.id.asc())
            .limit(1)
        )
        if oldest is not None:
            await delete_memory(db, owner_id, oldest.id)
            await db.commit()


async def write_temporary_fact(
    db,
    *,
    owner_id: str,
    conversation_id: str,
    turn_id: str,
    text,
    subject: str | None,
) -> TemporaryFactResult:
    """Write one conversation-scoped fact through the shared claim/fingerprint path.

    ``db`` may be a session, a session factory or an engine, like :class:`MemoryService`.
    """
    cleaned = _clean_text(text)
    if cleaned is None or _rejected(cleaned):
        return TemporaryFactResult(TemporaryFactOutcome.REJECTED)

    provided_subject = subject.strip() if isinstance(subject, str) and subject.strip() else None
    resolved_subject = provided_subject or resolve_subject(cleaned)
    key = _request_key(turn_id, cleaned)
    service = MemoryService(db, _NoEmbedding())

    async with service.sessions() as session:
        try:
            await owned_conversation(session, owner_id, conversation_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return TemporaryFactResult(TemporaryFactOutcome.CHAT_GONE)
            raise

        replayed = await session.scalar(
            select(MemoryWriteRequest).where(
                MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key == key
            )
        )
        if replayed is not None and replayed.state == "completed" and replayed.result is not None:
            return TemporaryFactResult(TemporaryFactOutcome.WRITTEN, MemoryResult(**replayed.result))

        permanent = await session.scalar(
            select(Memory.id)
            .where(
                Memory.owner_id == owner_id,
                Memory.status == "active",
                Memory.scope == SCOPE_GLOBAL,
                or_(
                    Memory.subject == resolved_subject,
                    func.lower(Memory.content) == cleaned.casefold(),
                ),
            )
            .limit(1)
        )
        if permanent is not None:
            return TemporaryFactResult(TemporaryFactOutcome.SKIPPED_PERMANENT)

        duplicate = await session.scalar(
            select(Memory)
            .where(
                Memory.owner_id == owner_id,
                Memory.status == "active",
                Memory.scope == SCOPE_CONVERSATION,
                Memory.source_conversation_id == conversation_id,
                func.lower(Memory.content) == cleaned.casefold(),
            )
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(1)
        )
        if duplicate is not None:
            return TemporaryFactResult(TemporaryFactOutcome.DUPLICATE, memory_result(duplicate))

        target = (
            await _active_temporary(
                session, owner_id=owner_id, conversation_id=conversation_id, subject=provided_subject
            )
            if provided_subject is not None
            else None
        )
        active_count = await session.scalar(
            select(func.count())
            .select_from(Memory)
            .where(
                Memory.owner_id == owner_id,
                Memory.status == "active",
                Memory.scope == SCOPE_CONVERSATION,
                Memory.source_conversation_id == conversation_id,
            )
        )

    try:
        if target is not None:
            result = await service.correct_memory(
                owner_id,
                target.id,
                target.version,
                cleaned,
                key,
                source_turn_id=turn_id,
                conversation_id=conversation_id,
                scope=SCOPE_CONVERSATION,
                subject=resolved_subject,
                embed=False,
            )
            outcome = TemporaryFactOutcome.REPLACED
        else:
            result = await service.create_memory(
                owner_id,
                conversation_id,
                turn_id,
                cleaned,
                key,
                scope=SCOPE_CONVERSATION,
                subject=resolved_subject,
                embed=False,
            )
            outcome = TemporaryFactOutcome.WRITTEN
    except HTTPException as exc:
        if exc.status_code == 404:
            return TemporaryFactResult(TemporaryFactOutcome.CHAT_GONE)
        raise

    if outcome is TemporaryFactOutcome.WRITTEN and (active_count or 0) >= MAX_TEMPORARY_PER_CHAT:
        await _evict_oldest(service, owner_id=owner_id, conversation_id=conversation_id, keep_id=result.id)
    return TemporaryFactResult(outcome, result)
