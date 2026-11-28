"""Write, deduplicate, supersede and cap conversation-scoped (temporary) memories.

Temporary memory is the key facts of one conversation, saved automatically with no consent step. It is always on,
regardless of the permanent-memory mode, and a row dies with its conversation. This module is the write path only; a
background producer calls it after a reply (a later change). It never writes a ``ContextOperation`` row.

Safety floor, applied before any write: a credential, an ID/card/phone number, an email or a referential text is
refused. A permanent fact with the same subject or content wins, because permanent memory is the owner's explicit
choice.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException
from sqlalchemy import and_, func, null, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import EMBEDDING_MODEL, SCOPE_CONVERSATION, SCOPE_GLOBAL
from app.domain.memory_capture import is_referential, resolve_subject
from app.domain.memory_policy import Sensitivity, classify_sensitivity, has_long_number, looks_like_phone
from app.infrastructure.database import Memory, MemoryWriteRequest, now
from app.services.authorization import owned_conversation
from app.services.memory import MemoryResult, MemoryService, memory_result, validate_embedding
from app.services.memory_erasure_lock import lock_owner_memories
from app.services.memory_terms import delete_terms, write_terms
from app.services.memory_visibility import delete_memory

MAX_TEMPORARY_CHARS = 200
MAX_TEMPORARY_SUBJECT_CHARS = 120
MAX_TEMPORARY_PER_CHAT = 30

logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)


class TemporaryFactOutcome(StrEnum):
    WRITTEN = "written"
    REPLACED = "replaced"
    DUPLICATE = "duplicate"
    SKIPPED_PERMANENT = "skipped_permanent"
    SKIPPED_HIDDEN = "skipped_hidden"
    REJECTED = "rejected"
    CHAT_GONE = "chat_gone"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class TemporaryFactResult:
    outcome: TemporaryFactOutcome
    memory: MemoryResult | None = None


class _NoEmbedding:
    """Temporary rows never call OpenAI; a call here is a bug, not a fallback."""

    async def embed(self, db, text):
        raise RuntimeError("temporary memories are lexical only")


class PromotionConflict(HTTPException):
    """The temporary row changed before it could be promoted; the caller may use its ordinary path."""

    def __init__(self):
        super().__init__(409, detail={"code": "MEMORY_PROMOTION_CONFLICT"})


def _clean_text(text) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned or len(cleaned) > MAX_TEMPORARY_CHARS or not cleaned.isprintable():
        return None
    return cleaned


def _rejected(text: str) -> bool:
    """The credential/ID floor: a fact carrying one of these is never written automatically."""
    if classify_sensitivity(text) is Sensitivity.CREDENTIAL:
        return True
    if has_long_number(text) or looks_like_phone(text):
        return True
    if _EMAIL.search(text) is not None:
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


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


async def _hidden_collision(
    db: AsyncSession, *, owner_id: str, conversation_id: str, subject: str, content: str
) -> bool:
    """A hidden fact of this owner or this chat must stay hidden, even when the writer sees it fresh.

    A global hidden row is visible everywhere and blocks every chat; a hidden temporary row is visible only in its
    own source chat. The content comparison is normalised in Python because SQL ``lower()`` and ``casefold()`` differ.
    """
    rows = await db.execute(
        select(Memory.content, Memory.subject).where(
            Memory.owner_id == owner_id,
            Memory.status.in_(("hidden", "deleted")),
            or_(
                Memory.scope == SCOPE_GLOBAL,
                and_(Memory.scope == SCOPE_CONVERSATION, Memory.source_conversation_id == conversation_id),
            ),
        )
    )
    wanted = _normalized(content)
    for stored_content, stored_subject in rows:
        if not stored_content:
            continue
        if stored_subject == subject or _normalized(stored_content) == wanted:
            return True
        if stored_subject is None and resolve_subject(stored_content) == subject:
            return True
    return False


async def _enforce_cap(service: MemoryService, *, owner_id: str, conversation_id: str, keep_id: str) -> None:
    """Evict every active temporary row above the cap; a failure after the commit is logged, never raised."""
    while True:
        try:
            async with service.sessions() as db:
                active = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(Memory)
                        .where(
                            Memory.owner_id == owner_id,
                            Memory.status == "active",
                            Memory.scope == SCOPE_CONVERSATION,
                            Memory.source_conversation_id == conversation_id,
                        )
                    )
                    or 0
                )
                if active <= MAX_TEMPORARY_PER_CHAT:
                    return
                oldest_id = await db.scalar(
                    select(Memory.id)
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
                if oldest_id is None:
                    return
                await delete_memory(db, owner_id, oldest_id, scrub_proposals=False)
                await db.commit()
        except Exception:
            # The fact is already committed. A cap overrun is preferable to failing the producer's write, and the
            # next write retries the eviction.
            logger.exception(
                "temporary memory cap eviction failed",
                extra={"owner_id": owner_id, "conversation_id": conversation_id},
            )
            return


async def _embed_promoted(service: MemoryService, embedder, result: MemoryResult, owner_id: str) -> None:
    """Best effort: a permanent row wants an embedding, but lexical recall must never depend on OpenAI."""
    if embedder is None:
        return
    try:
        async with service.sessions() as embed_db:
            vector = validate_embedding(await embedder.embed(embed_db, result.content))
        async with service.sessions() as db, db.begin():
            await db.execute(
                update(Memory)
                .where(
                    Memory.id == result.id,
                    Memory.owner_id == owner_id,
                    Memory.version == result.version,
                    Memory.status == "active",
                    Memory.scope == SCOPE_GLOBAL,
                    Memory.embedding.is_(None),
                )
                .values(embedding=list(vector), embedding_model=EMBEDDING_MODEL)
            )
    except Exception:
        logger.exception(
            "temporary memory promotion embedding failed",
            extra={"owner_id": owner_id, "memory_id": result.id},
        )


async def promote_temporary_memory(
    db,
    *,
    owner_id: str,
    conversation_id: str,
    memory_id: str,
    expected_version: int,
    content: str | None = None,
    subject: str | None = None,
    embedder=None,
    guard=None,
) -> MemoryResult:
    """Promote one active temporary row to a permanent row IN PLACE (same id, no superseded row).

    The conversation row lock comes first, then the owner lock, matching every erase path. The conditional UPDATE is
    the live/ownership/scope/version re-check: exactly one row or a typed conflict. Embedding is attempted after the
    transaction commits; a failure leaves it NULL and never fails the save.
    """
    service = MemoryService(db, embedder or _NoEmbedding())
    async with service.sessions() as session, session.begin():
        await owned_conversation(session, owner_id, conversation_id, lock=True, key_share=True)
        await lock_owner_memories(session, owner_id)
        if guard is not None:
            await guard(session)
        values = {
            "scope": SCOPE_GLOBAL,
            "version": Memory.version + 1,
            "updated_at": now(),
            # A permanent row must not inherit a stale embedding from a different text.
            "embedding": null(),
        }
        if content is not None:
            values["content"] = content
            values["subject"] = subject or resolve_subject(content)
        elif subject is not None:
            values["subject"] = subject
        changed = await session.execute(
            update(Memory)
            .where(
                Memory.id == memory_id,
                Memory.owner_id == owner_id,
                Memory.status == "active",
                Memory.scope == SCOPE_CONVERSATION,
                Memory.source_conversation_id == conversation_id,
                Memory.version == expected_version,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise PromotionConflict()
        if content is not None:
            await delete_terms(session, [memory_id])
            await write_terms(session, memory_id=memory_id, owner_id=owner_id, content=content)
        row = await session.scalar(
            select(Memory)
            .where(Memory.id == memory_id, Memory.owner_id == owner_id)
            .execution_options(populate_existing=True)
        )
        result = memory_result(row)
    await _embed_promoted(service, embedder, result, owner_id)
    return result


async def _precheck(
    session: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    key: str,
    resolved_subject: str,
    cleaned: str,
) -> TemporaryFactResult | None:
    """Return an outcome when no write is needed; ``None`` means the caller may write."""
    replayed = await session.scalar(
        select(MemoryWriteRequest).where(MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key == key)
    )
    if replayed is not None and replayed.state == "completed" and replayed.result is not None:
        return TemporaryFactResult(TemporaryFactOutcome.WRITTEN, MemoryResult(**replayed.result))

    if await _hidden_collision(
        session,
        owner_id=owner_id,
        conversation_id=conversation_id,
        subject=resolved_subject,
        content=cleaned,
    ):
        return TemporaryFactResult(TemporaryFactOutcome.SKIPPED_HIDDEN)

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
    return None


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
    if subject is not None and not isinstance(subject, str):
        return TemporaryFactResult(TemporaryFactOutcome.REJECTED)
    provided_subject = subject.strip() if isinstance(subject, str) and subject.strip() else None
    if provided_subject is not None and (
        len(provided_subject) > MAX_TEMPORARY_SUBJECT_CHARS or _rejected(provided_subject)
    ):
        return TemporaryFactResult(TemporaryFactOutcome.REJECTED)
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

        prechecked = await _precheck(
            session,
            owner_id=owner_id,
            conversation_id=conversation_id,
            key=key,
            resolved_subject=resolved_subject,
            cleaned=cleaned,
        )
        if prechecked is not None:
            return prechecked

        # The stored subject is the resolved one, even when the caller passed None.
        target = await _active_temporary(
            session, owner_id=owner_id, conversation_id=conversation_id, subject=resolved_subject
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
        if exc.status_code in {400, 403, 409, 422}:
            # A concurrent replace/delete or a rejected request is an expected outcome, not a producer crash.
            return TemporaryFactResult(TemporaryFactOutcome.CONFLICT)
        raise

    if outcome is TemporaryFactOutcome.WRITTEN:
        await _enforce_cap(service, owner_id=owner_id, conversation_id=conversation_id, keep_id=result.id)
    return TemporaryFactResult(outcome, result)
