"""Owner-scoped hide, unhide and permanent delete of one memory.

Hide is memory-level (every conversation), reversible and needs no consent.
Delete is a tombstone: status ``deleted`` with content, subject and embedding
wiped so foreign keys stay valid.  Both use ``Memory.status`` (a plain string),
so no migration is needed; every reader already filters on ``status == 'active'``
and the shared exclusion lineage also blocks hidden/deleted rows for derived
history.  Delete is reachable only from the authenticated UI route, never from
voice or a model tool.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory_changes import AUTO_SAVE
from app.infrastructure.database import (
    ContextOperation,
    Memory,
    MemoryCaptureDecision,
    MemoryWriteRequest,
    Turn,
    now,
)

ACTIVE = "active"
HIDDEN = "hidden"
DELETED = "deleted"
HIDE_OPERATION = "memory_hide"
UNHIDE_OPERATION = "memory_unhide"
_LINEAGE_LIMIT = 100


@dataclass(frozen=True)
class VisibilityResult:
    id: str
    status: str
    changed: bool


async def _owned(db: AsyncSession, owner_id: str, memory_id: str) -> Memory:
    row = await db.scalar(select(Memory).where(Memory.id == memory_id, Memory.owner_id == owner_id))
    if row is None:
        raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"})
    return row


async def _transition(db: AsyncSession, owner_id: str, memory_id: str, *, source: str, target: str) -> VisibilityResult:
    row = await _owned(db, owner_id, memory_id)
    if row.status == target:
        return VisibilityResult(row.id, target, False)
    if row.status == DELETED:
        raise HTTPException(409, detail={"code": "MEMORY_DELETED"})
    if row.status != source:
        raise HTTPException(409, detail={"code": "MEMORY_NOT_AVAILABLE"})
    changed = await db.execute(
        update(Memory)
        .where(Memory.id == memory_id, Memory.owner_id == owner_id, Memory.status == source)
        .values(status=target, updated_at=now())
    )
    if changed.rowcount != 1:
        # Lost a race to another transition: report the state that won, never fake success.
        await db.refresh(row)
        if row.status == target:
            return VisibilityResult(row.id, target, False)
        raise HTTPException(409, detail={"code": "MEMORY_NOT_AVAILABLE"})
    return VisibilityResult(memory_id, target, True)


async def hide_memory(db: AsyncSession, owner_id: str, memory_id: str) -> VisibilityResult:
    return await _transition(db, owner_id, memory_id, source=ACTIVE, target=HIDDEN)


async def unhide_memory(db: AsyncSession, owner_id: str, memory_id: str) -> VisibilityResult:
    return await _transition(db, owner_id, memory_id, source=HIDDEN, target=ACTIVE)


async def _lineage(db: AsyncSession, owner_id: str, row: Memory) -> list[Memory]:
    """The memory and the earlier versions it corrected, which carry the same fact."""
    chain = [row]
    seen = {row.id}
    current = row
    while current.supersedes_id is not None and len(chain) < _LINEAGE_LIMIT and current.supersedes_id not in seen:
        previous = await db.scalar(
            select(Memory).where(Memory.id == current.supersedes_id, Memory.owner_id == owner_id)
        )
        if previous is None:
            break
        chain.append(previous)
        seen.add(previous.id)
        current = previous
    return chain


def _tombstoned_request(result: dict) -> dict:
    """Keep the lookup keys a replay reads and drop the proposal text and signed token."""
    proposal = result.get("proposal")
    kept: dict = {"scrubbed": True}
    if isinstance(proposal, dict):
        if isinstance(proposal.get("confirmation_id"), str):
            kept["confirmation_id"] = proposal["confirmation_id"]
        if isinstance(proposal.get("confirmation_revision"), int):
            kept["confirmation_revision"] = proposal["confirmation_revision"]
    tombstone: dict = {"proposal": kept, "scrubbed": True}
    if isinstance(result.get("decision"), str):
        tombstone["decision"] = result["decision"]
    return tombstone


def _tombstoned_snapshot(proposal: dict) -> dict:
    return {
        "confirmation_id": proposal.get("confirmation_id"),
        "confirmation_revision": proposal.get("confirmation_revision"),
        "scrubbed": True,
    }


def _request_memory_id(result: dict) -> str | None:
    response = result.get("response")
    memory = response.get("memory") if isinstance(response, dict) else None
    return memory.get("id") if isinstance(memory, dict) else None


async def _scrub_proposals(db: AsyncSession, owner_id: str, deleted: set[str]) -> set[str]:
    """Tombstone the proposal rows whose saved memory is in the deleted lineage."""
    proposals = (await db.scalars(select(MemoryWriteRequest).where(MemoryWriteRequest.owner_id == owner_id))).all()
    proposal_keys: set[str] = set()
    for request in proposals:
        result = request.result
        if not isinstance(result, dict) or _request_memory_id(result) not in deleted:
            continue
        stored = result.get("proposal")
        if isinstance(stored, dict) and isinstance(stored.get("confirmation_id"), str):
            proposal_keys.add(stored["confirmation_id"])
        proposal_keys.add(request.request_key)
        request.result = _tombstoned_request(result)
        request.embedding = None
    return proposal_keys


async def _scrub_decisions(db: AsyncSession, owner_id: str, deleted: set[str], proposal_keys: set[str]) -> None:
    decisions = (
        await db.scalars(select(MemoryCaptureDecision).where(MemoryCaptureDecision.owner_id == owner_id))
    ).all()
    for decision in decisions:
        result = decision.result
        if not isinstance(result, dict):
            continue
        memory = result.get("memory")
        belongs = decision.confirmation_id in proposal_keys or (
            isinstance(memory, dict) and memory.get("id") in deleted
        )
        if belongs:
            decision.result = {"status": "discarded", "memory": None, "scrubbed": True}


async def _scrub_turns(db: AsyncSession, chain: list[Memory], proposal_keys: set[str]) -> None:
    source_turns = [item.source_turn_id for item in chain if item.source_turn_id]
    if not source_turns:
        return
    turns = (await db.scalars(select(Turn).where(Turn.id.in_(source_turns)))).all()
    for turn in turns:
        snapshot = dict(turn.snapshot or {})
        proposal = snapshot.get("memory_proposal")
        if isinstance(proposal, dict) and proposal.get("confirmation_id") in proposal_keys:
            snapshot["memory_proposal"] = _tombstoned_snapshot(proposal)
            turn.snapshot = snapshot


async def delete_memory(db: AsyncSession, owner_id: str, memory_id: str) -> VisibilityResult:
    """Idempotent permanent tombstone; wipes content everywhere this service stores it."""
    row = await _owned(db, owner_id, memory_id)
    if row.status == DELETED:
        return VisibilityResult(row.id, DELETED, False)
    chain = await _lineage(db, owner_id, row)
    ids = [item.id for item in chain]
    deleted = set(ids)
    keys = [item.request_key for item in chain if item.request_key]
    await db.execute(
        update(Memory)
        .where(Memory.id.in_(ids), Memory.owner_id == owner_id)
        .values(status=DELETED, content="", subject=None, embedding=None, updated_at=now())
    )
    if keys:
        await db.execute(
            update(MemoryWriteRequest)
            .where(MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key.in_(keys))
            .values(embedding=None, result=None)
        )
    proposal_keys = await _scrub_proposals(db, owner_id, deleted)
    await _scrub_decisions(db, owner_id, deleted, proposal_keys)
    await _scrub_turns(db, chain, proposal_keys)
    operations = (
        await db.scalars(
            select(ContextOperation).where(
                ContextOperation.owner_id == owner_id,
                ContextOperation.command_type.in_((HIDE_OPERATION, UNHIDE_OPERATION, AUTO_SAVE)),
            )
        )
    ).all()
    for operation in operations:
        if (operation.payload or {}).get("memory_id") in ids:
            # Spoken lines quote the memory; keep the id (for audit) and drop the text.
            operation.payload = {"memory_id": (operation.payload or {}).get("memory_id"), "scrubbed": True}
            if operation.outcome == "applied" and operation.command_type in (HIDE_OPERATION, AUTO_SAVE):
                operation.outcome = "deleted"
    return VisibilityResult(memory_id, DELETED, True)
