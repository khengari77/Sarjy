"""Consent-first voice memory exclusion: target lookup, proposal, confirm and Undo.

The signed token is the authority for what may be excluded.  A pending proposal
is recorded as a namespaced ``ContextOperation`` row (the existing durable
operation store) so a later "yes" can only apply the exact fact the user was
asked about.  Nothing is excluded on the first turn, and a stale, expired,
replayed or cross-conversation token never changes context state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.commands import CommandKind, CommandResult
from app.domain.context_operations import sign_proposal, verify_proposal
from app.infrastructure.database import ContextExclusion, ContextOperation, Memory, now
from app.services.authorization import owned_conversation
from app.services.context_operations import (
    EXCLUDE,
    ContextOperationError,
    MemorySearcher,
    confirm_proposal,
    create_exclusion_proposal,
    has_content_words,
    keyword_search,
    undo_operation,
)
from app.services.memory_erasure_lock import lock_owner_memories
from app.services.memory_scope import visible_in
from app.services.memory_terms import all_visible_memories, search_all_memories
from app.services.memory_visibility import (
    ACTIVE,
    DELETED,
    HIDDEN,
    HIDE_OPERATION,
    UNHIDE_OPERATION,
    unhide_memory,
)

EXCLUDE_PROPOSAL = "exclude_proposal"
RESTORE_PROPOSAL = "restore_proposal"
HIDE_PROPOSAL = HIDE_OPERATION
PROPOSAL_TYPES = (EXCLUDE_PROPOSAL, RESTORE_PROPOSAL, HIDE_PROPOSAL)
PENDING = "proposed"
APPLIED = "applied"
DECLINED = "declined"
SUPERSEDED = "superseded"
EXPIRED = "expired"
RESOLVED = (APPLIED, DECLINED, EXPIRED)
TTL_SECONDS = 300
MATCH_LIMIT = 10
# One bind-safe bulk update.  Postgres and SQLite both accept far more, but a
# fixed chunk keeps a large "forget everything" bounded and predictable.
HIDE_CHUNK_SIZE = 500
# A definite refusal from the context service means the signed request can
# never become valid again: discard it instead of inviting a retry.
STALE_CODES = frozenset(
    {
        "NO_CONTEXT_CHANGE",
        "MEMORY_NOT_FOUND",
        "PROPOSAL_EXPIRED",
        "PROPOSAL_INVALID",
        "REVISION_CONFLICT",
        "OPERATION_ALREADY_RESTORED",
        "OPERATION_NOT_FOUND",
        "OPERATION_NOT_RESTORABLE",
    }
)


def _request_key(owner_id: str, conversation_id: str, command_type: str, request_id: str) -> str:
    digest = hashlib.sha256("\x1f".join((owner_id, conversation_id, command_type, request_id)).encode()).hexdigest()
    return f"voice:{command_type}:{digest}"[:100]


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _snippet(text: str, limit: int = 100) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _expires_at() -> int:
    return int((datetime.now(UTC) + timedelta(seconds=TTL_SECONDS)).timestamp())


def _now_timestamp() -> int:
    return int(datetime.now(UTC).timestamp())


def _unchanged(request_id: str, detail: str, spoken: str) -> CommandResult:
    return CommandResult(request_id=request_id, kind=CommandKind.UNCHANGED, spoken=spoken, detail=detail)


def _needs_confirmation(request_id: str, detail: str, spoken: str) -> CommandResult:
    return CommandResult(request_id=request_id, kind=CommandKind.NEEDS_CONFIRMATION, spoken=spoken, detail=detail)


def _unsupported(request_id: str, detail: str, spoken: str) -> CommandResult:
    return CommandResult(request_id=request_id, kind=CommandKind.UNSUPPORTED, spoken=spoken, detail=detail)


def _applied(request_id: str, detail: str, spoken: str, operation_id: str) -> CommandResult:
    return CommandResult(
        request_id=request_id,
        kind=CommandKind.EXECUTED,
        changed=True,
        spoken=spoken,
        detail=detail,
        operation_id=operation_id,
    )


def _failed(request_id: str, detail: str) -> CommandResult:
    return CommandResult(
        request_id=request_id,
        kind=CommandKind.FAILED,
        spoken="I couldn't apply that. Nothing was changed.",
        detail=detail,
    )


def _response(row: ContextOperation) -> CommandResult | None:
    stored = (row.payload or {}).get("response")
    if not isinstance(stored, dict):
        return None
    return CommandResult.model_validate(stored)


def _number_word(count: int) -> str:
    words = {1: "one", 2: "two", 3: "three"}
    return words.get(count, str(count))


def _ambiguity_spoken(candidates) -> str:
    names = [_snippet(candidate.content, 55) for candidate in candidates[:3]]
    if len(candidates) == 2:
        listing = f"{names[0]} and {names[1]}"
    elif len(candidates) == 3:
        listing = f"{names[0]}, {names[1]}, and {names[2]}"
    else:
        listing = f"{names[0]}, {names[1]}, and {names[2]}, plus {len(candidates) - 3} more"
    return f"I have {_number_word(len(candidates))} matches: {listing}. Which one?"


async def _supersede_pending(db: AsyncSession, owner_id: str, conversation_id: str) -> bool:
    """Retire any pending proposal; report whether one existed."""
    pending_id = await db.scalar(
        select(ContextOperation.id)
        .where(
            ContextOperation.owner_id == owner_id,
            ContextOperation.conversation_id == conversation_id,
            ContextOperation.command_type.in_(PROPOSAL_TYPES),
            ContextOperation.outcome == PENDING,
        )
        .limit(1)
    )
    if pending_id is None:
        return False
    result = await db.execute(
        update(ContextOperation)
        .where(
            ContextOperation.owner_id == owner_id,
            ContextOperation.conversation_id == conversation_id,
            ContextOperation.command_type.in_(PROPOSAL_TYPES),
            ContextOperation.outcome == PENDING,
        )
        .values(outcome=SUPERSEDED)
    )
    return result.rowcount > 0


async def discard_pending(db: AsyncSession, *, owner_id: str, conversation_id: str) -> None:
    """Disarm the pending proposal when the user moves to another memory request."""
    # Every utterance checks this: only write and commit when a proposal is armed.
    if await _supersede_pending(db, owner_id, conversation_id):
        await db.commit()


async def _record_pending(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    command_type: str,
    payload: dict,
    request_id: str,
    revision: int,
    operation_id: str | None = None,
) -> None:
    await _supersede_pending(db, owner_id, conversation_id)
    db.add(
        ContextOperation(
            id=operation_id or str(uuid4()),
            owner_id=owner_id,
            conversation_id=conversation_id,
            command_type=command_type,
            payload=payload,
            revision_before=revision,
            revision_after=None,
            idempotency_key=_request_key(owner_id, conversation_id, command_type, request_id),
            outcome=PENDING,
            created_at=now(),
        )
    )
    await db.commit()


async def _replay_proposal(
    db: AsyncSession, *, owner_id: str, conversation_id: str, command_type: str, request_id: str
) -> CommandResult | None:
    row = await db.scalar(
        select(ContextOperation).where(
            ContextOperation.idempotency_key == _request_key(owner_id, conversation_id, command_type, request_id)
        )
    )
    return _response(row) if row is not None else None


async def _pending(db: AsyncSession, *, owner_id: str, conversation_id: str) -> ContextOperation | None:
    return await db.scalar(
        select(ContextOperation)
        .where(
            ContextOperation.owner_id == owner_id,
            ContextOperation.conversation_id == conversation_id,
            ContextOperation.command_type.in_(PROPOSAL_TYPES),
            ContextOperation.outcome == PENDING,
        )
        .order_by(ContextOperation.created_at.desc(), ContextOperation.id.desc())
        .limit(1)
    )


async def _resolved_replay(
    db: AsyncSession, *, owner_id: str, conversation_id: str, request_id: str
) -> CommandResult | None:
    rows = (
        await db.scalars(
            select(ContextOperation)
            .where(
                ContextOperation.owner_id == owner_id,
                ContextOperation.conversation_id == conversation_id,
                ContextOperation.command_type.in_(PROPOSAL_TYPES),
                ContextOperation.outcome.in_(RESOLVED),
            )
            .order_by(ContextOperation.created_at.desc(), ContextOperation.id.desc())
            .limit(10)
        )
    ).all()
    for row in rows:
        if (row.payload or {}).get("confirmation_request_id") == request_id:
            return _response(row)
    return None


async def _finish_pending(
    db: AsyncSession, row: ContextOperation, outcome: str, response: CommandResult, request_id: str
) -> None:
    payload = dict(row.payload or {})
    payload["confirmation_request_id"] = request_id
    payload["response"] = response.model_dump(mode="json")
    row.payload = payload
    row.outcome = outcome
    await db.commit()


async def _stale(db: AsyncSession, row: ContextOperation, request_id: str, detail: str, spoken: str) -> CommandResult:
    response = _unchanged(request_id, detail, spoken)
    await _finish_pending(db, row, SUPERSEDED, response, request_id)
    return response


async def propose_exclusion(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    target: str,
    request_id: str,
    secret: str,
    search: MemorySearcher = keyword_search,
) -> CommandResult:
    """Find the target, then ask for consent without changing context."""
    if not secret:
        return _unsupported(
            request_id,
            "CONTEXT_PROPOSALS_NOT_CONFIGURED",
            "Memory changes by voice aren't available right now. Nothing was changed.",
        )
    replay = await _replay_proposal(
        db, owner_id=owner_id, conversation_id=conversation_id, command_type=EXCLUDE_PROPOSAL, request_id=request_id
    )
    if replay is not None:
        return replay
    # A new request always retires the previous question, even when this one
    # turns out to be ambiguous or unmatched.
    await discard_pending(db, owner_id=owner_id, conversation_id=conversation_id)
    candidates = tuple(
        await search(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            query=target,
            limit=MATCH_LIMIT,
        )
    )
    if not candidates:
        return _unchanged(
            request_id,
            "MEMORY_TARGET_NOT_FOUND",
            f"I couldn't find anything matching {_snippet(target, 100)}; nothing was changed.",
        )
    if len(candidates) > 1:
        return _needs_confirmation(request_id, "MEMORY_TARGET_AMBIGUOUS", _ambiguity_spoken(candidates))

    candidate = candidates[0]
    expires_at = _expires_at()
    proposal = await create_exclusion_proposal(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        memory_ids=(candidate.id,),
        secret=secret,
        expires_at=expires_at,
    )
    response = _needs_confirmation(
        request_id,
        "MEMORY_EXCLUSION_PROPOSED",
        f"Exclude '{_snippet(candidate.content, 90)}' from this conversation? Say yes to exclude, or no.",
    )
    await _record_pending(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        command_type=EXCLUDE_PROPOSAL,
        payload={
            "kind": "exclude",
            "token": proposal["proposal"],
            "memory_id": candidate.id,
            "content": candidate.content,
            "expires_at": expires_at,
            "response": response.model_dump(mode="json"),
        },
        request_id=request_id,
        revision=proposal["revision"],
    )
    return response


async def _record_operation(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    command_type: str,
    request_id: str,
    payload: dict,
    outcome: str,
) -> None:
    conversation = await owned_conversation(db, owner_id, conversation_id)
    db.add(
        ContextOperation(
            id=str(uuid4()),
            owner_id=owner_id,
            conversation_id=conversation_id,
            command_type=command_type,
            payload=payload,
            revision_before=conversation.revision,
            revision_after=None,
            idempotency_key=_request_key(owner_id, conversation_id, command_type, request_id),
            outcome=outcome,
            created_at=now(),
        )
    )


def _topic_phrase(target: str, all_memories: bool) -> str:
    return "you" if all_memories else _snippet(target, 80)


def _preview_spoken(target: str, all_memories: bool, count: int) -> str:
    noun = "memory" if count == 1 else "memories"
    action = "Hide it?" if count == 1 else "Hide all of them?"
    return f"I found {count} {noun} about {_topic_phrase(target, all_memories)}. {action}"


def _hidden_spoken(target: str, all_memories: bool, hidden: int, total: int) -> str:
    topic = _topic_phrase(target, all_memories)
    if hidden == total:
        noun = "memory" if hidden == 1 else "memories"
        pronoun = "it" if hidden == 1 else "them"
        return f"Hidden {hidden} {noun} about {topic}. Say undo to bring {pronoun} back."
    if hidden == 0:
        return "None of those memories could be hidden; they had already changed."
    skipped = total - hidden
    return f"Hidden {hidden} of {total}; {_number_word(skipped)} had already changed."


def _payload_memory_ids(payload: dict) -> list[str]:
    raw = payload.get("memory_ids")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    single = payload.get("memory_id")
    return [single] if isinstance(single, str) else []


async def _hide_memories_in_chunks(
    db: AsyncSession, *, owner_id: str, conversation_id: str, memory_ids: Sequence[str]
) -> int:
    hidden = 0
    for start in range(0, len(memory_ids), HIDE_CHUNK_SIZE):
        chunk = list(memory_ids[start : start + HIDE_CHUNK_SIZE])
        result = await db.execute(
            update(Memory)
            .where(
                Memory.id.in_(chunk),
                Memory.owner_id == owner_id,
                Memory.status == ACTIVE,
                visible_in(conversation_id),
            )
            .values(status=HIDDEN, updated_at=now())
        )
        hidden += result.rowcount
    return hidden


async def hide_target(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    target: str,
    request_id: str,
    secret: str = "",
    all_memories: bool = False,
    search: MemorySearcher | None = None,
) -> CommandResult:
    """Preview hiding every matching memory, then apply the exact ids on one yes.

    A named topic uses the uncapped lexical index.  ``all_memories`` previews every
    visible active memory (global plus this chat's temporary rows); another chat's
    temporary memory is never included.  The pending operation row owns the exact
    ids; the signed token carries no ids and only proves the count and expiry.
    """
    if not all_memories and not has_content_words(target):
        # Reached from the voice grammar and from the model's forget tool alike. An empty keyword
        # query matches every memory, so a target that names nothing must never search.
        return _unchanged(
            request_id, "MEMORY_TARGET_UNSPECIFIED", "Tell me which memory to forget. Nothing was changed."
        )
    replay = await _replay_proposal(
        db, owner_id=owner_id, conversation_id=conversation_id, command_type=HIDE_PROPOSAL, request_id=request_id
    )
    if replay is not None:
        return replay
    # A new memory request always retires the previous question.
    await discard_pending(db, owner_id=owner_id, conversation_id=conversation_id)
    if all_memories:
        candidates = tuple(await all_visible_memories(db, owner_id=owner_id, conversation_id=conversation_id))
    elif search is not None:
        candidates = tuple(
            await search(db, owner_id=owner_id, conversation_id=conversation_id, query=target, limit=MATCH_LIMIT)
        )
    else:
        candidates = tuple(
            await search_all_memories(db, owner_id=owner_id, conversation_id=conversation_id, query=target)
        )
    if not candidates:
        return _unchanged(
            request_id,
            "MEMORY_TARGET_NOT_FOUND",
            f"I couldn't find anything about {_topic_phrase(target, all_memories)}; nothing was changed.",
        )
    if not secret:
        return _unsupported(
            request_id,
            "CONTEXT_PROPOSALS_NOT_CONFIGURED",
            "Memory changes by voice aren't available right now. Nothing was changed.",
        )
    conversation = await owned_conversation(db, owner_id, conversation_id)
    memory_ids = [candidate.id for candidate in candidates]
    count = len(memory_ids)
    expires_at = _expires_at()
    operation_id = str(uuid4())
    token = sign_proposal(
        {
            "v": 1,
            "kind": "hide_topic",
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "operation_id": operation_id,
            "revision": conversation.revision,
            "count": count,
            "exp": expires_at,
        },
        secret,
    )
    response = _needs_confirmation(request_id, "MEMORY_HIDE_PROPOSED", _preview_spoken(target, all_memories, count))
    await _record_pending(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        command_type=HIDE_PROPOSAL,
        payload={
            "kind": "hide_topic",
            "token": token,
            "query": target,
            "all_memories": all_memories,
            "memory_ids": memory_ids,
            "count": count,
            "expires_at": expires_at,
            "response": response.model_dump(mode="json"),
        },
        request_id=request_id,
        revision=conversation.revision,
        operation_id=operation_id,
    )
    return response


UNDO_SCAN_LIMIT = 10
UNDO_SCAN_PAGES = 5


async def _hide_operations_page(db: AsyncSession, owner_id: str, offset: int) -> Sequence[ContextOperation]:
    return (
        await db.scalars(
            select(ContextOperation)
            .where(
                ContextOperation.owner_id == owner_id,
                ContextOperation.command_type == HIDE_OPERATION,
                ContextOperation.outcome == "applied",
            )
            .order_by(ContextOperation.created_at.desc(), ContextOperation.id.desc())
            .limit(UNDO_SCAN_LIMIT)
            .offset(offset)
        )
    ).all()


def _restored_list_spoken(
    restored_ids: Sequence[str],
    *,
    total: int,
    deleted: int,
    already: int,
    unavailable: int,
    contents: dict[str, str],
) -> str:
    restored = len(restored_ids)
    if restored == total:
        if total == 1:
            content = contents.get(restored_ids[0], "")
            return f"Brought back '{_snippet(content, 80)}'." if content else "Brought back 1 memory."
        return f"Brought back {restored} memories."
    skipped = total - restored
    if deleted == skipped:
        reason = "was deleted" if skipped == 1 else "were deleted"
    elif already == skipped:
        reason = "was already visible" if skipped == 1 else "were already visible"
    elif unavailable == skipped:
        reason = "was unavailable" if skipped == 1 else "were unavailable"
    else:
        reason = "had already changed"
    return f"Brought back {restored} of {total}; {_number_word(skipped)} {reason}."


async def _undo_memory_list(
    db: AsyncSession,
    *,
    operation: ContextOperation,
    memory_ids: Sequence[str],
    total: int,
    erased: int,
    owner_id: str,
    conversation_id: str,
    request_id: str,
) -> CommandResult:
    rows: dict[str, tuple[str, str]] = {}
    for start in range(0, len(memory_ids), HIDE_CHUNK_SIZE):
        chunk = list(memory_ids[start : start + HIDE_CHUNK_SIZE])
        result = await db.execute(
            select(Memory.id, Memory.status, Memory.content).where(
                Memory.id.in_(chunk),
                Memory.owner_id == owner_id,
                visible_in(conversation_id),
            )
        )
        for row in result:
            rows[row.id] = (row.status, row.content)
    restored_ids: list[str] = []
    deleted = erased
    already = unavailable = 0
    for memory_id in memory_ids:
        stored = rows.get(memory_id)
        if stored is None:
            unavailable += 1
            continue
        status, _content = stored
        if status == DELETED:
            deleted += 1
            continue
        if status == ACTIVE:
            already += 1
            continue
        if status != HIDDEN:
            unavailable += 1
            continue
        changed = await db.execute(
            update(Memory)
            .where(
                Memory.id == memory_id,
                Memory.owner_id == owner_id,
                Memory.status == HIDDEN,
                visible_in(conversation_id),
            )
            .values(status=ACTIVE, updated_at=now())
        )
        if changed.rowcount == 1:
            restored_ids.append(memory_id)
        else:
            unavailable += 1
    spoken = _restored_list_spoken(
        restored_ids,
        total=total,
        deleted=deleted,
        already=already,
        unavailable=unavailable,
        contents={memory_id: content for memory_id, (_status, content) in rows.items()},
    )
    operation.outcome = "undone"
    response = (
        _applied(request_id, "MEMORY_UNHIDDEN", spoken, operation.id)
        if restored_ids
        else _unchanged(request_id, "MEMORY_ALREADY_VISIBLE", spoken)
    )
    await _record_operation(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        command_type=UNHIDE_OPERATION,
        request_id=request_id,
        payload={"memory_ids": restored_ids, "response": response.model_dump(mode="json")},
        outcome="applied",
    )
    await db.commit()
    return response


async def undo_last_hide(db: AsyncSession, *, owner_id: str, conversation_id: str, request_id: str) -> CommandResult:
    """Unhide the most recent hide visible in this conversation; one step, no consent."""
    replay = await _replay_proposal(
        db, owner_id=owner_id, conversation_id=conversation_id, command_type=UNHIDE_OPERATION, request_id=request_id
    )
    if replay is not None:
        return replay
    await discard_pending(db, owner_id=owner_id, conversation_id=conversation_id)
    # The scan is owner-wide and cannot filter by visibility in SQL (the memory id lives in the payload), so it pages:
    # a run of newer hides of other conversations' temporary memories must not hide an older visible one.
    operations = [
        operation
        for offset in range(0, UNDO_SCAN_LIMIT * UNDO_SCAN_PAGES, UNDO_SCAN_LIMIT)
        for operation in await _hide_operations_page(db, owner_id, offset)
    ]
    for operation in operations:
        payload = operation.payload or {}
        if isinstance(payload.get("memory_ids"), list):
            memory_ids = _payload_memory_ids(payload)
            if not memory_ids:
                continue
            stored_count = payload.get("count")
            total = (
                stored_count if isinstance(stored_count, int) and stored_count >= len(memory_ids) else len(memory_ids)
            )
            stored_erased = payload.get("erased_count")
            erased = stored_erased if isinstance(stored_erased, int) and 0 < stored_erased <= total else 0
            return await _undo_memory_list(
                db,
                operation=operation,
                memory_ids=memory_ids,
                total=total,
                erased=erased,
                owner_id=owner_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
        memory_id = payload.get("memory_id")
        if not isinstance(memory_id, str):
            continue
        content = await db.scalar(
            select(Memory.content).where(
                Memory.id == memory_id,
                Memory.owner_id == owner_id,
                visible_in(conversation_id),
            )
        )
        if content is None:
            # Another conversation's temporary memory: never unhide or quote it here.
            continue
        try:
            result = await unhide_memory(db, owner_id, memory_id)
        except HTTPException:
            await db.rollback()
            continue
        operation.outcome = "undone"
        response = (
            _applied(request_id, "MEMORY_UNHIDDEN", f"Brought back '{_snippet(content or '', 80)}'.", memory_id)
            if result.changed
            else _unchanged(request_id, "MEMORY_ALREADY_VISIBLE", "That memory is already visible.")
        )
        await _record_operation(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            command_type=UNHIDE_OPERATION,
            request_id=request_id,
            payload={"memory_id": memory_id, "response": response.model_dump(mode="json")},
            outcome="applied",
        )
        await db.commit()
        return response
    return _unchanged(request_id, "NO_HIDDEN_MEMORY", "There is nothing to undo.")


async def _latest_active_exclusion(
    db: AsyncSession, *, owner_id: str, conversation_id: str
) -> tuple[ContextOperation, tuple[str, ...]] | None:
    operations = (
        await db.scalars(
            select(ContextOperation)
            .where(
                ContextOperation.owner_id == owner_id,
                ContextOperation.conversation_id == conversation_id,
                ContextOperation.command_type == EXCLUDE,
            )
            .order_by(ContextOperation.created_at.desc(), ContextOperation.id.desc())
            .limit(20)
        )
    ).all()
    for operation in operations:
        memory_ids = (
            await db.scalars(
                select(ContextExclusion.memory_id).where(
                    ContextExclusion.operation_id == operation.id,
                    ContextExclusion.owner_id == owner_id,
                    ContextExclusion.conversation_id == conversation_id,
                    ContextExclusion.active.is_(True),
                    ContextExclusion.memory_id.in_(
                        select(Memory.id).where(Memory.owner_id == owner_id, visible_in(conversation_id))
                    ),
                )
            )
        ).all()
        if not memory_ids:
            continue
        contents = (
            await db.scalars(select(Memory.content).where(Memory.id.in_(memory_ids), Memory.owner_id == owner_id))
        ).all()
        return operation, tuple(contents)
    return None


def _restore_question(contents: tuple[str, ...]) -> str:
    if len(contents) == 1:
        return f"Restore '{_snippet(contents[0], 90)}' to this conversation? Say yes to restore, or no."
    return (
        f"Restore {_number_word(len(contents))} memories you excluded from this conversation? "
        "Say yes to restore, or no."
    )


async def propose_restore(
    db: AsyncSession, *, owner_id: str, conversation_id: str, request_id: str, secret: str
) -> CommandResult:
    """Ask for consent to undo the latest conversation-scoped exclusion."""
    if not secret:
        return _unsupported(
            request_id,
            "CONTEXT_PROPOSALS_NOT_CONFIGURED",
            "Memory changes by voice aren't available right now. Nothing was changed.",
        )
    replay = await _replay_proposal(
        db, owner_id=owner_id, conversation_id=conversation_id, command_type=RESTORE_PROPOSAL, request_id=request_id
    )
    if replay is not None:
        return replay
    await discard_pending(db, owner_id=owner_id, conversation_id=conversation_id)
    conversation = await owned_conversation(db, owner_id, conversation_id)
    latest = await _latest_active_exclusion(db, owner_id=owner_id, conversation_id=conversation_id)
    if latest is None:
        return _unchanged(
            request_id,
            "NO_EXCLUDED_MEMORIES",
            "There are no excluded memories in this conversation to restore.",
        )
    operation, contents = latest
    expires_at = _expires_at()
    token = sign_proposal(
        {
            "v": 1,
            "kind": "restore",
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "revision": conversation.revision,
            "operation_id": operation.id,
            "exp": expires_at,
        },
        secret,
    )
    response = _needs_confirmation(request_id, "MEMORY_RESTORE_PROPOSED", _restore_question(contents))
    await _record_pending(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        command_type=RESTORE_PROPOSAL,
        payload={
            "kind": "restore",
            "token": token,
            "operation_id": operation.id,
            "expires_at": expires_at,
            "response": response.model_dump(mode="json"),
        },
        request_id=request_id,
        revision=conversation.revision,
    )
    return response


async def _apply_exclusion(
    db: AsyncSession,
    *,
    pending: ContextOperation,
    owner_id: str,
    conversation_id: str,
    token: str,
    request_id: str,
    secret: str,
) -> CommandResult:
    conversation = await owned_conversation(db, owner_id, conversation_id)
    try:
        result = await confirm_proposal(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            proposal=token,
            expected_revision=conversation.revision,
            idempotency_key=_token_key(token),
            secret=secret,
        )
    except ContextOperationError as exc:
        if exc.code in STALE_CODES:
            return await _stale(
                db,
                pending,
                request_id,
                exc.code,
                "That exclusion request is no longer valid. Nothing was changed.",
            )
        await _finish_pending(db, pending, SUPERSEDED, _failed(request_id, exc.code), request_id)
        return _failed(request_id, exc.code)
    content = str((pending.payload or {}).get("content") or "that memory")
    response = _applied(
        request_id,
        "MEMORY_EXCLUDED",
        f"Excluded '{_snippet(content, 80)}' from this conversation. Say undo that exclusion to restore it.",
        result["operation_id"],
    )
    await _finish_pending(db, pending, APPLIED, response, request_id)
    return response


async def _apply_restore(
    db: AsyncSession,
    *,
    pending: ContextOperation,
    owner_id: str,
    conversation_id: str,
    token: str,
    verified: dict,
    request_id: str,
) -> CommandResult:
    conversation = await owned_conversation(db, owner_id, conversation_id)
    if verified.get("revision") != conversation.revision:
        return await _stale(
            db,
            pending,
            request_id,
            "REVISION_CONFLICT",
            "That restore request is no longer valid. Nothing was changed.",
        )
    operation_id = verified.get("operation_id")
    if not isinstance(operation_id, str):
        return await _stale(
            db, pending, request_id, "PROPOSAL_INVALID", "That restore request is no longer valid. Nothing was changed."
        )
    try:
        result = await undo_operation(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            operation_id=operation_id,
            expected_revision=conversation.revision,
            idempotency_key=_token_key(token),
        )
    except ContextOperationError as exc:
        if exc.code in STALE_CODES:
            return await _stale(
                db,
                pending,
                request_id,
                exc.code,
                "That restore request is no longer valid. Nothing was changed.",
            )
        await _finish_pending(db, pending, SUPERSEDED, _failed(request_id, exc.code), request_id)
        return _failed(request_id, exc.code)
    restored = result.get("restored_ids") or []
    count = len(restored)
    spoken = (
        "Restored the memory to this conversation."
        if count == 1
        else f"Restored {count} memories to this conversation."
    )
    response = _applied(request_id, "MEMORY_RESTORED", spoken, result["operation_id"])
    await _finish_pending(db, pending, APPLIED, response, request_id)
    return response


async def _apply_hide_topic(
    db: AsyncSession,
    *,
    pending: ContextOperation,
    owner_id: str,
    conversation_id: str,
    request_id: str,
    verified: dict,
) -> CommandResult:
    payload = pending.payload or {}
    if verified.get("operation_id") != pending.id:
        return await _stale(
            db, pending, request_id, "PROPOSAL_INVALID", "That request is no longer valid. Nothing was changed."
        )
    memory_ids = _payload_memory_ids(payload)
    if not memory_ids:
        return await _stale(
            db, pending, request_id, "PROPOSAL_INVALID", "That request is no longer valid. Nothing was changed."
        )
    stored_count = payload.get("count")
    total = stored_count if isinstance(stored_count, int) and stored_count >= len(memory_ids) else len(memory_ids)
    try:
        # The conversation lock comes first, then the per-owner memory lock: the same order every
        # erase/write path uses, so a concurrent delete and this hide cannot interleave.
        await owned_conversation(db, owner_id, conversation_id, lock=True)
        await lock_owner_memories(db, owner_id)
        hidden = await _hide_memories_in_chunks(
            db, owner_id=owner_id, conversation_id=conversation_id, memory_ids=memory_ids
        )
    except HTTPException as exc:
        await db.rollback()
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else "MEMORY_HIDE_FAILED"
        return _failed(request_id, str(code))
    except Exception:
        await db.rollback()
        return _failed(request_id, "MEMORY_HIDE_FAILED")
    spoken = _hidden_spoken(str(payload.get("query") or ""), bool(payload.get("all_memories")), hidden, total)
    response = (
        _applied(request_id, "MEMORY_HIDDEN", spoken, pending.id)
        if hidden
        else _unchanged(request_id, "MEMORY_ALREADY_CHANGED", spoken)
    )
    await _finish_pending(db, pending, APPLIED, response, request_id)
    return response


async def _apply_pending(
    db: AsyncSession, *, pending: ContextOperation, owner_id: str, conversation_id: str, request_id: str, secret: str
) -> CommandResult:
    payload = pending.payload or {}
    token = payload.get("token")
    if not isinstance(token, str) or not secret:
        return await _stale(
            db, pending, request_id, "PROPOSAL_INVALID", "That request is no longer valid. Nothing was changed."
        )
    try:
        verified = verify_proposal(token, secret)
    except ValueError:
        return await _stale(
            db, pending, request_id, "PROPOSAL_INVALID", "That request is no longer valid. Nothing was changed."
        )
    if verified.get("owner_id") != owner_id or verified.get("conversation_id") != conversation_id:
        return await _stale(
            db, pending, request_id, "PROPOSAL_INVALID", "That request is no longer valid. Nothing was changed."
        )
    expires_at = verified.get("exp")
    if isinstance(expires_at, int) and expires_at < _now_timestamp():
        return await _stale(db, pending, request_id, "PROPOSAL_EXPIRED", "That request expired. Nothing was changed.")
    kind = payload.get("kind")
    if kind == "exclude":
        return await _apply_exclusion(
            db,
            pending=pending,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            request_id=request_id,
            secret=secret,
        )
    if kind == "restore":
        return await _apply_restore(
            db,
            pending=pending,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            verified=verified,
            request_id=request_id,
        )
    if kind == "hide_topic":
        return await _apply_hide_topic(
            db,
            pending=pending,
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            verified=verified,
        )
    return await _stale(
        db, pending, request_id, "PROPOSAL_INVALID", "That request is no longer valid. Nothing was changed."
    )


async def resolve_confirmation(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    confirmed: bool,
    request_id: str,
    secret: str,
) -> CommandResult | None:
    """Apply or discard the pending proposal; ``None`` means "not a command"."""
    replay = await _resolved_replay(db, owner_id=owner_id, conversation_id=conversation_id, request_id=request_id)
    if replay is not None:
        return replay
    pending = await _pending(db, owner_id=owner_id, conversation_id=conversation_id)
    if pending is None:
        return None
    if not confirmed:
        response = _unchanged(request_id, "MEMORY_CHANGE_DECLINED", "Okay, I won't change anything.")
        await _finish_pending(db, pending, DECLINED, response, request_id)
        return response
    return await _apply_pending(
        db,
        pending=pending,
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        secret=secret,
    )


def global_retraction_refusal(request_id: str) -> CommandResult:
    return _unsupported(
        request_id,
        "MEMORY_GLOBAL_RETRACTION_UNAVAILABLE",
        "I can't delete a memory from every conversation by voice yet. Nothing was changed.",
    )


__all__ = [
    "global_retraction_refusal",
    "hide_target",
    "propose_exclusion",
    "propose_restore",
    "resolve_confirmation",
    "undo_last_hide",
]
