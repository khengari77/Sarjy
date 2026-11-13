"""Transactional shell for D2.7 context proposals, exclusions and Undo.

Wire format and authorization
-----------------------------
* Preview is read-only: it signs an opaque proposal token containing the exact
  candidate ids and the conversation revision.  No rows are written.
* Confirmation trusts only the signed token.  Ids supplied by a client are
  never read, and every id is re-authorized against the owner's active
  memories before the single transaction commits.
* Idempotency is stored on ``ContextOperation.idempotency_key`` (a namespaced
  SHA-256 digest, so owner/conversation scopes cannot collide).  A repeat
  returns the stored outcome without adding exclusions or advancing revisions.
"""

import hashlib
import re
from collections.abc import Sequence
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context_operations import (
    MemoryCandidate,
    classify,
    exclusion_diff,
    restore_diff,
    select_candidates,
    sign_proposal,
    verify_proposal,
)
from app.infrastructure.database import (
    ContextExclusion,
    ContextOperation,
    ContextRevision,
    Memory,
    SessionGate,
    now,
)
from app.services.authorization import owned_conversation
from app.services.memory_eligibility import blocked_memory_ids

SCOPE = "conversation"
EXCLUDE = "exclude"
RESTORE = "restore"
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "about",
        "all",
        "anything",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "chat",
        "conversation",
        "do",
        "does",
        "everything",
        "for",
        "from",
        "get",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "remember",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "with",
        "you",
        "your",
    ]
)
TOKEN = re.compile(r"[0-9A-Za-z\u0600-\u06ff]+")


class ContextOperationError(Exception):
    """Typed failure mapped to an API error code by the route layer."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


class MemorySearcher(Protocol):
    """Narrow seam for #5's MemoryService.search_memories."""

    async def __call__(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        conversation_id: str,
        query: str,
        limit: int,
    ) -> Sequence[MemoryCandidate]: ...


def _tokens(text: str) -> set[str]:
    return {token for token in TOKEN.findall(text.casefold()) if token not in STOPWORDS}


async def keyword_search(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    query: str,
    limit: int,
) -> Sequence[MemoryCandidate]:
    """Deterministic fallback retrieval against the shared memory table.

    Applies the same owner-scoped correction-lineage exclusions as semantic
    retrieval so injected/test adapters cannot drift from production policy.
    """
    rows = (
        await db.scalars(
            select(Memory)
            .where(Memory.owner_id == owner_id, Memory.status == "active")
            .order_by(Memory.created_at.desc())
        )
    ).all()
    wanted = _tokens(query)
    scored: list[MemoryCandidate] = []
    for row in rows:
        content_tokens = _tokens(row.content)
        overlap = len(wanted & content_tokens)
        if not wanted or overlap:
            score = float(overlap) if wanted else 0.0
            scored.append(
                MemoryCandidate(
                    id=row.id,
                    content=row.content,
                    source_turn_id=row.source_turn_id,
                    score=score,
                    kind=classify(row.content),
                )
            )
    scored.sort(key=lambda candidate: (-candidate.score, candidate.id))
    excluded = await blocked_memory_ids(db, owner_id, conversation_id)
    return tuple(item for item in scored if item.id not in excluded)[:limit]


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _operation_key(owner_id: str, conversation_id: str, kind: str, idempotency_key: str) -> str:
    return _digest("context-operation", owner_id, conversation_id, kind, idempotency_key)


async def _replay(db: AsyncSession, key: str, owner_id: str, conversation_id: str, request_hash: str) -> dict | None:
    existing = await db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
    if existing is None:
        return None
    if existing.owner_id != owner_id or existing.conversation_id != conversation_id:
        # Namespaced keys make this unreachable, but never leak another scope.
        raise ContextOperationError("OPERATION_NOT_FOUND", 404)
    payload = existing.payload or {}
    if payload.get("request_hash") != request_hash:
        raise ContextOperationError("IDEMPOTENCY_KEY_REUSED")
    response = dict(payload.get("response") or {})
    response["idempotent"] = True
    return response


async def create_proposal(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    query: str,
    keep: tuple[str, ...] = (),
    limit: int = 20,
    secret: str,
    search: MemorySearcher = keyword_search,
) -> dict:
    """Build a preview without writing anything. Read-only by construction."""
    if not secret:
        raise ContextOperationError("CONTEXT_PROPOSALS_NOT_CONFIGURED", 503)
    conversation = await owned_conversation(db, owner_id, conversation_id)
    candidates = tuple(await search(db, owner_id=owner_id, conversation_id=conversation_id, query=query, limit=limit))
    # The owner filter is re-applied defensively for adapters that might return
    # foreign rows; preview must never surface another tenant's memory.
    rows = (
        await db.scalars(
            select(Memory).where(
                Memory.id.in_([candidate.id for candidate in candidates] or ["-"]),
                Memory.owner_id == owner_id,
                Memory.status == "active",
            )
        )
    ).all()
    owned = {row.id for row in rows}
    candidates = tuple(candidate for candidate in candidates if candidate.id in owned)
    selection = select_candidates(candidates, keep)
    revision = conversation.revision
    token = sign_proposal(
        {
            "v": 1,
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "revision": revision,
            "query": query,
            "keep": list(keep),
            "excluded_ids": sorted(selection.excluded_ids),
            "kept_ids": sorted(selection.kept_ids),
        },
        secret,
    )
    return {
        "conversation_id": conversation_id,
        "query": query,
        "revision": revision,
        "proposal": token,
        "matches": [
            {
                "id": candidate.id,
                "content": candidate.content,
                "source_turn_id": candidate.source_turn_id,
                "score": candidate.score,
                "kind": "budget" if candidate.is_budget else candidate.kind,
            }
            for candidate in selection.matches
        ],
        "preserved": [
            {"id": record.id, "content": record.content, "reason": record.reason} for record in selection.preserved
        ],
        "diff": selection.diff(revision, revision),
    }


async def confirm_proposal(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    proposal: str,
    expected_revision: int,
    idempotency_key: str,
    secret: str,
    commit: bool = True,
) -> dict:
    if not secret:
        raise ContextOperationError("CONTEXT_PROPOSALS_NOT_CONFIGURED", 503)
    await db.execute(update(SessionGate).where(SessionGate.id == 1).values(marker=1))
    conversation = await owned_conversation(db, owner_id, conversation_id, lock=True)
    payload = _verified_payload(proposal, secret, owner_id, conversation_id)
    excluded_ids, kept_ids = _exclusion_ids(payload)
    request_hash = _digest(EXCLUDE, proposal, str(expected_revision))
    key = _operation_key(owner_id, conversation_id, EXCLUDE, idempotency_key)
    replayed = await _replay(db, key, owner_id, conversation_id, request_hash)
    if replayed is not None:
        return replayed

    _validate_exclusion(payload, expected_revision, conversation, excluded_ids, kept_ids)
    await _authorize_exclusion_rows(db, owner_id, excluded_ids, kept_ids)

    before = conversation.revision
    after = before + 1
    conversation.revision = after
    operation_id = str(uuid4())
    diff = exclusion_diff(excluded_ids, kept_ids, before, after)
    response = {
        "operation_id": operation_id,
        "status": "applied",
        "idempotent": False,
        "excluded_ids": sorted(excluded_ids),
        "kept_ids": sorted(kept_ids),
        "revision_before": before,
        "revision_after": after,
        "diff": diff,
    }
    db.add(
        ContextOperation(
            id=operation_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
            command_type=EXCLUDE,
            payload={
                "query": payload.get("query", ""),
                "excluded_ids": sorted(excluded_ids),
                "kept_ids": sorted(kept_ids),
                "diff": diff,
                "request_hash": request_hash,
                "response": response,
            },
            revision_before=before,
            revision_after=after,
            idempotency_key=key,
            outcome="applied",
            created_at=now(),
        )
    )
    # Flush the operation first so PostgreSQL satisfies the exclusion FK order.
    await db.flush()
    db.add(
        ContextRevision(
            id=str(uuid4()), conversation_id=conversation_id, revision=after, created_by=owner_id, created_at=now()
        )
    )
    for memory_id in sorted(excluded_ids):
        db.add(
            ContextExclusion(
                id=str(uuid4()),
                owner_id=owner_id,
                conversation_id=conversation_id,
                memory_id=memory_id,
                operation_id=operation_id,
                scope=SCOPE,
                active=True,
                created_at=now(),
            )
        )
    return await _finish(
        db,
        commit=commit,
        key=key,
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_hash=request_hash,
        response=response,
    )


def _verified_payload(proposal: str, secret: str, owner_id: str, conversation_id: str) -> dict:
    try:
        payload = verify_proposal(proposal, secret)
    except ValueError as exc:
        raise ContextOperationError("PROPOSAL_INVALID", 400) from exc
    if payload.get("owner_id") != owner_id or payload.get("conversation_id") != conversation_id:
        raise ContextOperationError("PROPOSAL_INVALID", 404)
    return payload


def _exclusion_ids(payload: dict) -> tuple[frozenset[str], frozenset[str]]:
    excluded = frozenset(str(value) for value in payload.get("excluded_ids", ()))
    kept = frozenset(str(value) for value in payload.get("kept_ids", ()))
    return excluded, kept


def _validate_exclusion(payload, expected_revision, conversation, excluded_ids, kept_ids) -> None:
    if payload.get("revision") != expected_revision or conversation.revision != expected_revision:
        raise ContextOperationError("REVISION_CONFLICT")
    if not excluded_ids:
        raise ContextOperationError("NO_CONTEXT_CHANGE")
    if excluded_ids & kept_ids:
        # Defence in depth: a keep-exception can never be smuggled away.
        raise ContextOperationError("PROPOSAL_INVALID", 400)


async def _authorize_exclusion_rows(db: AsyncSession, owner_id: str, excluded_ids, kept_ids) -> None:
    rows = (
        await db.scalars(
            select(Memory).where(
                Memory.id.in_(list(excluded_ids) or ["-"]),
                Memory.owner_id == owner_id,
                Memory.status == "active",
            )
        )
    ).all()
    if {row.id for row in rows} != excluded_ids:
        raise ContextOperationError("PROPOSAL_INVALID")
    if any(classify(row.content) == "budget" or row.id in kept_ids for row in rows):
        raise ContextOperationError("PROPOSAL_INVALID", 400)


async def _finish(
    db: AsyncSession, *, commit: bool, key: str, owner_id: str, conversation_id: str, request_hash: str, response: dict
) -> dict:
    try:
        await db.flush()
        if commit:
            await db.commit()
    except IntegrityError:
        if not commit:
            raise
        await db.rollback()
        replayed = await _replay(db, key, owner_id, conversation_id, request_hash)
        if replayed is None:
            raise
        return replayed
    return response


async def _undo_operation(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    operation_id: str,
    expected_revision: int,
    idempotency_key: str,
    commit: bool = True,
) -> dict:
    await db.execute(update(SessionGate).where(SessionGate.id == 1).values(marker=1))
    conversation = await owned_conversation(db, owner_id, conversation_id, lock=True)
    original = await db.scalar(
        select(ContextOperation).where(
            ContextOperation.id == operation_id,
            ContextOperation.owner_id == owner_id,
            ContextOperation.conversation_id == conversation_id,
        )
    )
    if original is None:
        raise ContextOperationError("OPERATION_NOT_FOUND", 404)
    request_hash = _digest(RESTORE, operation_id, str(expected_revision))
    key = _operation_key(owner_id, conversation_id, RESTORE, idempotency_key)
    replayed = await _replay(db, key, owner_id, conversation_id, request_hash)
    if replayed is not None:
        return replayed

    if original.command_type != EXCLUDE:
        raise ContextOperationError("OPERATION_NOT_RESTORABLE")
    if conversation.revision != expected_revision or original.revision_after is None:
        raise ContextOperationError("REVISION_CONFLICT")

    exclusions = (
        await db.scalars(
            select(ContextExclusion).where(
                ContextExclusion.operation_id == original.id,
                ContextExclusion.owner_id == owner_id,
                ContextExclusion.conversation_id == conversation_id,
            )
        )
    ).all()
    active = [row for row in exclusions if row.active]
    if not active:
        raise ContextOperationError("OPERATION_ALREADY_RESTORED")

    for row in active:
        row.active = False
    restored_ids = frozenset(row.memory_id for row in active)

    before = conversation.revision
    after = before + 1
    conversation.revision = after
    still_excluded = frozenset(
        (
            await db.scalars(
                select(ContextExclusion.memory_id).where(
                    ContextExclusion.owner_id == owner_id,
                    ContextExclusion.conversation_id == conversation_id,
                    ContextExclusion.active.is_(True),
                )
            )
        ).all()
    )
    diff = restore_diff(restored_ids, still_excluded, before, after)
    restore_id = str(uuid4())
    response = {
        "operation_id": restore_id,
        "restored_operation_id": original.id,
        "status": "restored",
        "idempotent": False,
        "restored_ids": sorted(restored_ids - still_excluded),
        "revision_before": before,
        "revision_after": after,
        "diff": diff,
    }
    db.add(
        ContextOperation(
            id=restore_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
            command_type=RESTORE,
            payload={
                "restored_operation_id": original.id,
                "restored_ids": sorted(restored_ids - still_excluded),
                "diff": diff,
                "request_hash": request_hash,
                "response": response,
            },
            revision_before=before,
            revision_after=after,
            idempotency_key=key,
            outcome="applied",
            created_at=now(),
        )
    )
    # Flush the operation first so PostgreSQL satisfies the exclusion FK order.
    await db.flush()
    db.add(
        ContextRevision(
            id=str(uuid4()), conversation_id=conversation_id, revision=after, created_by=owner_id, created_at=now()
        )
    )
    try:
        await db.flush()
        if commit:
            await db.commit()
    except IntegrityError:
        if not commit:
            raise
        await db.rollback()
        replayed = await _replay(db, key, owner_id, conversation_id, request_hash)
        if replayed is None:
            raise
        return replayed
    return response


async def list_operations(db: AsyncSession, *, owner_id: str, conversation_id: str) -> dict:
    conversation = await owned_conversation(db, owner_id, conversation_id)
    rows = (
        await db.scalars(
            select(ContextOperation)
            .where(
                ContextOperation.owner_id == owner_id,
                ContextOperation.conversation_id == conversation_id,
                ContextOperation.command_type.in_((EXCLUDE, RESTORE)),
            )
            .order_by(ContextOperation.created_at.desc(), ContextOperation.id)
        )
    ).all()
    return {
        "conversation_id": conversation_id,
        "revision": conversation.revision,
        "operations": [
            {
                "id": row.id,
                "command_type": row.command_type,
                "outcome": row.outcome,
                "revision_before": row.revision_before,
                "revision_after": row.revision_after,
                "idempotency_key": row.idempotency_key,
                "created_at": row.created_at,
                "diff": (row.payload or {}).get("diff"),
            }
            for row in rows
        ],
    }


async def context_state(db: AsyncSession, *, owner_id: str, conversation_id: str) -> dict:
    conversation = await owned_conversation(db, owner_id, conversation_id)
    excluded_ids = (
        await db.scalars(
            select(ContextExclusion.memory_id).where(
                ContextExclusion.owner_id == owner_id,
                ContextExclusion.conversation_id == conversation_id,
                ContextExclusion.active.is_(True),
            )
        )
    ).all()
    return {
        "conversation_id": conversation_id,
        "revision": conversation.revision,
        "excluded_ids": sorted(set(excluded_ids)),
    }


async def undo_operation(db, *, owner_id, conversation_id, operation_id, expected_revision, idempotency_key):
    """Public transaction-owning entrypoint."""
    return await _undo_operation(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        operation_id=operation_id,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
    )


async def undo_operation_in_transaction(
    db, *, owner_id, conversation_id, operation_id, expected_revision, idempotency_key
):
    """Restricted dispatcher core: the caller alone owns commit/rollback."""
    return await _undo_operation(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        operation_id=operation_id,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        commit=False,
    )
