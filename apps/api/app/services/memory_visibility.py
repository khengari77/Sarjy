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

import json
import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import Text, cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context import prompt_sha256
from app.domain.memory_changes import AUTO_SAVE
from app.infrastructure.database import (
    ContextOperation,
    Conversation,
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


async def _owned(db: AsyncSession, owner_id: str, memory_id: str, *, lock: bool = False) -> Memory:
    query = select(Memory).where(Memory.id == memory_id, Memory.owner_id == owner_id)
    if lock:
        # populate_existing: the row may have changed while this transaction waited for its lock.
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(query)
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


def _norm(value: object) -> str:
    return " ".join(str(value).casefold().split()) if isinstance(value, str) else ""


async def _scrub_proposals(db: AsyncSession, owner_id: str, deleted: set[str], texts: set[str]) -> set[str]:
    """Tombstone the proposal rows of the deleted facts.

    A row belongs to the deletion when its saved memory is in the lineage, or when it proposed exactly the deleted
    text (a proposal left claimed by a crash or a racing confirm never recorded its memory id). A tombstoned row is
    marked discarded, so a confirm that finishes later cannot write the fact back into it.
    """
    proposals = (await db.scalars(select(MemoryWriteRequest).where(MemoryWriteRequest.owner_id == owner_id))).all()
    proposal_keys: set[str] = set()
    wanted = {_norm(text) for text in texts if _norm(text)}
    for request in proposals:
        result = request.result
        if not isinstance(result, dict):
            continue
        stored = result.get("proposal")
        proposed_text = _norm(stored.get("text")) if isinstance(stored, dict) else ""
        bound = _request_memory_id(result)
        # Text alone matches only a row that never recorded a memory; one bound to another live memory is kept.
        if bound not in deleted and (bound is not None or proposed_text not in wanted):
            continue
        if isinstance(stored, dict) and isinstance(stored.get("confirmation_id"), str):
            proposal_keys.add(stored["confirmation_id"])
        proposal_keys.add(request.request_key)
        request.result = _tombstoned_request(result)
        request.embedding = None
        if request.state in ("proposed", "claimed"):
            request.state = "discarded"
    return proposal_keys


async def _scrub_decisions(db: AsyncSession, owner_id: str, deleted: set[str], proposal_keys: set[str]) -> None:
    decisions = (
        await db.scalars(select(MemoryCaptureDecision).where(MemoryCaptureDecision.owner_id == owner_id))
    ).all()
    for decision in decisions:
        result = decision.result
        memory = result.get("memory") if isinstance(result, dict) else None
        belongs = decision.confirmation_id in proposal_keys or (
            isinstance(memory, dict) and memory.get("id") in deleted
        )
        if not belongs:
            continue
        # A decision still applying has no result yet (result is NULL), so test it before looking at the result.
        decision.result = {"status": "discarded", "memory": None, "scrubbed": True}
        # A confirm already past its claim must lose the lease, or it would write the fact back.
        decision.outcome = "discarded"
        decision.lease_id = None
        decision.lease_expires_at = None


async def _scrub_turns(db: AsyncSession, owner_id: str, chain: list[Memory], proposal_keys: set[str]) -> None:
    source_turns = [item.source_turn_id for item in chain if item.source_turn_id]
    if not source_turns:
        return
    turns = (
        await db.scalars(
            select(Turn).where(
                Turn.id.in_(source_turns),
                Turn.conversation_id.in_(select(Conversation.id).where(Conversation.owner_id == owner_id)),
            )
        )
    ).all()
    for turn in turns:
        snapshot = dict(turn.snapshot or {})
        proposal = snapshot.get("memory_proposal")
        if isinstance(proposal, dict) and proposal.get("confirmation_id") in proposal_keys:
            snapshot["memory_proposal"] = _tombstoned_snapshot(proposal)
            turn.snapshot = snapshot


# Word characters, plus Arabic combining marks (harakat), which Python's \\w does not count.
_WORD = r"\w\u0610-\u061a\u064b-\u065f"


def _text_variants(texts: set[str]) -> set[str]:
    """The fact as it can appear in a stored prompt: plain, and JSON-escaped (inside a memory envelope)."""
    variants: set[str] = set()
    for text in texts:
        if not text.strip():
            continue
        variants |= {text, json.dumps(text)[1:-1], json.dumps(text, ensure_ascii=False)[1:-1]}
        variants |= {f'"text":{json.dumps(text)}', f'"text":{json.dumps(text, ensure_ascii=False)}'}
    return {variant for variant in variants if variant}


def _without(value: object, needles: set[str]) -> object:
    """Remove each needle as a whole run (never from inside a longer word, so 'male' leaves 'female' alone)."""
    if isinstance(value, str):
        for needle in sorted(needles, key=len, reverse=True):
            value = re.sub(rf"(?<![{_WORD}]){re.escape(needle)}(?![{_WORD}])", "", value)
        return value
    if isinstance(value, list):
        return [_without(item, needles) for item in value]
    if isinstance(value, dict):
        return {key: _without(item, needles) for key, item in value.items()}
    return value


def _rehashed(snapshot: object) -> object:
    """Keep the stored prompt digest consistent with the scrubbed messages."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("messages"), list):
        return snapshot
    try:
        digest = prompt_sha256(snapshot["messages"])
    except (KeyError, TypeError):
        return snapshot
    result = dict(snapshot)
    if "canonical_prompt_sha256" in result:
        result["canonical_prompt_sha256"] = digest
    manifest = result.get("manifest")
    if isinstance(manifest, dict) and "canonical_prompt_sha256" in manifest:
        result["manifest"] = {**manifest, "canonical_prompt_sha256": digest}
    return result


async def _scrub_snapshots(db: AsyncSession, owner_id: str, deleted: set[str], texts: set[str]) -> None:
    """Remove the fact from the stored compiled prompts (turn snapshots) of this owner that carried it."""
    needles = _text_variants(texts)
    if not needles or not deleted:
        return
    owned = select(Conversation.id).where(Conversation.owner_id == owner_id)
    turns = (
        await db.scalars(
            select(Turn).where(
                Turn.conversation_id.in_(owned),
                or_(*[cast(Turn.snapshot, Text).like(f"%{memory_id}%") for memory_id in sorted(deleted)]),
            )
        )
    ).all()
    for turn in turns:
        scrubbed = _without(turn.snapshot, needles)
        if scrubbed != turn.snapshot:
            turn.snapshot = _rehashed(scrubbed)


async def _live_ids_with_text(db: AsyncSession, owner_id: str, texts: set[str]) -> set[str]:
    if not texts:
        return set()
    rows = await db.scalars(
        select(Memory.id).where(Memory.owner_id == owner_id, Memory.status != DELETED, Memory.content.in_(texts))
    )
    return set(rows)


async def _wait_for_in_flight_writes(db: AsyncSession, owner_id: str) -> None:
    """Block until every confirm already committing for this owner has finished.

    Lock order is the one the confirm's committing transaction uses: write-request rows, then decision rows, then
    memory rows. Taking them in another order would let a delete and a correction wait on each other.
    """
    await db.execute(select(MemoryWriteRequest.id).where(MemoryWriteRequest.owner_id == owner_id).with_for_update())
    await db.execute(
        select(MemoryCaptureDecision.confirmation_id)
        .where(MemoryCaptureDecision.owner_id == owner_id)
        .with_for_update()
    )


async def delete_memory(db: AsyncSession, owner_id: str, memory_id: str) -> VisibilityResult:
    """Idempotent permanent tombstone; wipes content everywhere this service stores it."""
    row = await _owned(db, owner_id, memory_id)
    if row.status == DELETED:
        return VisibilityResult(row.id, DELETED, False)
    chain_texts = {item.content for item in await _lineage(db, owner_id, row) if item.content}
    before = await _live_ids_with_text(db, owner_id, chain_texts)
    # A confirm past its claim holds its rows while it commits a memory. Wait for it (and fence any that start
    # later: their guard refuses a discarded proposal), then take the memory row, so a correction that is
    # superseding it has committed and the lineage below includes it.
    await _wait_for_in_flight_writes(db, owner_id)
    row = await _owned(db, owner_id, memory_id, lock=True)
    if row.status == DELETED:
        return VisibilityResult(row.id, DELETED, False)
    chain = await _lineage(db, owner_id, row)
    # Whatever the confirm saved with the deleted text meanwhile is the fact written back as a new row.
    raced = await _live_ids_with_text(db, owner_id, chain_texts | {item.content for item in chain if item.content})
    raced -= before | {item.id for item in chain}
    if raced:
        chain = [*chain, *(await db.scalars(select(Memory).where(Memory.id.in_(raced), Memory.owner_id == owner_id)))]
    ids = [item.id for item in chain]
    deleted = set(ids)
    texts = {item.content for item in chain if item.content}
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
    proposal_keys = await _scrub_proposals(db, owner_id, deleted, texts)
    await _scrub_decisions(db, owner_id, deleted, proposal_keys)
    await _scrub_turns(db, owner_id, chain, proposal_keys)
    await _scrub_snapshots(db, owner_id, deleted, texts)
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
