"""Read-only, user-safe projection of one turn's stored context snapshot.

The snapshot is the authoritative record of what the compiler gave the model.
This service exposes only the parts a user may see: conversation text, memory
and tool data, excluded record ids with a reason, and a budget summary. System
instructions, the pipeline configuration, the provider request and excluded
content are never read back from the snapshot.
"""

import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.domain.context import (
    DEFAULT_MAX_CHARS,
    untrusted_data_kind,
    untrusted_data_prefix,
)
from app.domain.restatement import restating_turn_ids
from app.infrastructure.database import ToolCall, Turn
from app.services.authorization import owned_conversation
from app.services.context_assembly import (
    RETRACTION_PREFIX,
    _memory_rows_for_ids,
    read_context_inputs,
)
from app.services.context_blocks import BLOCK_TEXT_LIMIT, _active_exclusion_ids

# How many preceding user turns are scanned for a compiled snapshot (legacy turns have none).
SNAPSHOT_LOOKBACK = 25


@dataclass(frozen=True)
class _SnapshotTurn:
    id: str
    created_at: str
    snapshot: dict


def _context_snapshot(value: object) -> dict | None:
    """Return a snapshot that carries a compiled message list, if it has one."""
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("messages"), list) or not isinstance(value.get("included_record_ids"), list):
        return None
    return value


def _snapshot_turn(turn: Turn) -> _SnapshotTurn | None:
    snapshot = _context_snapshot(turn.snapshot)
    if snapshot is None:
        return None
    return _SnapshotTurn(turn.id, turn.created_at, snapshot)


async def _user_snapshot_before(
    db: AsyncSession,
    *,
    conversation_id: str,
    created_at: str,
    turn_id: str,
) -> _SnapshotTurn | None:
    """Nearest preceding user turn of this conversation with a compiled snapshot."""
    rows = (
        await db.execute(
            select(Turn.id, Turn.created_at, Turn.snapshot)
            .where(
                Turn.conversation_id == conversation_id,
                Turn.role == "user",
                Turn.snapshot.is_not(None),
                or_(
                    Turn.created_at < created_at,
                    and_(Turn.created_at == created_at, Turn.id < turn_id),
                ),
            )
            .order_by(Turn.created_at.desc(), Turn.id.desc())
            .limit(SNAPSHOT_LOOKBACK)
        )
    ).all()
    for row_id, row_created_at, row_snapshot in rows:
        snapshot = _context_snapshot(row_snapshot)
        if snapshot is not None:
            return _SnapshotTurn(row_id, row_created_at, snapshot)
    return None


def _included_text(record_id: str, content: str, kind: str | None) -> str:
    """Recover the original record text from a memory/tool envelope."""
    if kind is None:
        return content
    prefix = untrusted_data_prefix(kind, record_id)
    try:
        payload = json.loads(content[len(prefix) :])
    except (TypeError, ValueError):
        return content
    if not isinstance(payload, dict):
        return content
    text = payload.get("text")
    return text if isinstance(text, str) else content


async def _redacted_memory_ids(
    db: AsyncSession, *, owner_id: str, record_ids: set[str], exclusions: set[str]
) -> set[str]:
    """Included memories that are now hidden, deleted or excluded in this conversation."""
    rows = await _memory_rows_for_ids(db, owner_id, record_ids)
    return {row.id for row in rows if row.status in ("hidden", "deleted")} | exclusions


async def _turn_texts(db: AsyncSession, *, conversation_id: str, record_ids: set[str]) -> dict[str, str]:
    """The included records that are real turns of this conversation (history, whatever their text says)."""
    if not record_ids:
        return {}
    rows = await db.execute(
        select(Turn.id, Turn.text).where(Turn.id.in_(record_ids), Turn.conversation_id == conversation_id)
    )
    return {row_id: text or "" for row_id, text in rows.all()}


def _record_ids(snapshot: object, key: str) -> set[str] | None:
    """A snapshot's list of record ids, or None when it is missing or unusable (unknown provenance)."""
    values = snapshot.get(key) if isinstance(snapshot, dict) else None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None
    return {value for value in values if not value.startswith(RETRACTION_PREFIX)}


def _reply_dependencies(turn: Turn, parent: Turn | None) -> set[str] | None:
    """What an assistant reply inherited, mirroring the lens: its own derivation, else its parent prompt's records."""
    snapshot = turn.snapshot if isinstance(turn.snapshot, dict) else {}
    derivation = snapshot.get("derivation")
    if isinstance(derivation, dict):
        values = _record_ids(derivation, "record_ids")
        if values is None:
            return None
        return values | ({parent.id} if parent is not None else set())
    if parent is None:
        return None
    inherited = _record_ids(parent.snapshot, "included_record_ids")
    return None if inherited is None else inherited | {parent.id}


async def _derived_ids(
    db: AsyncSession, *, conversation_id: str, included_ids: set[str], blocked: set[str]
) -> set[str]:
    """Included assistant replies and tool results that inherit a blocked record, however old they are.

    The lens only follows derivation inside its bounded window, so the receipt repeats it for the snapshot's own
    records: a reply (or tool result) derived from something now blocked can paraphrase a forgotten fact without
    sharing enough words to be caught as a restatement. Unknown provenance fails closed while anything is blocked.
    """
    if not blocked or not included_ids:
        return set()
    columns = load_only(Turn.id, Turn.conversation_id, Turn.request_id, Turn.role, Turn.snapshot)
    turns = (
        await db.scalars(
            select(Turn).options(columns).where(Turn.id.in_(included_ids), Turn.conversation_id == conversation_id)
        )
    ).all()
    calls = (await db.scalars(select(ToolCall).where(ToolCall.id.in_(included_ids)))).all()
    reply_parents = {turn.request_id.removeprefix("reply:") for turn in turns if turn.request_id.startswith("reply:")}
    parent_ids = reply_parents | {call.turn_id for call in calls}
    parents = {
        turn.id: turn
        for turn in (
            await db.scalars(
                select(Turn).options(columns).where(Turn.id.in_(parent_ids), Turn.conversation_id == conversation_id)
            )
        ).all()
    }
    dependencies: dict[str, set[str] | None] = {}
    for turn in turns:
        if turn.role == "assistant":
            parent = (
                parents.get(turn.request_id.removeprefix("reply:")) if turn.request_id.startswith("reply:") else None
            )
            dependencies[turn.id] = _reply_dependencies(turn, parent)
    for call in calls:
        parent = parents.get(call.turn_id)
        inherited = _record_ids(parent.snapshot, "included_record_ids") if parent is not None else None
        dependencies[call.id] = None if inherited is None or parent is None else inherited | {parent.id}
    derived: set[str] = set()
    while True:
        more = {
            record_id
            for record_id, references in dependencies.items()
            if record_id not in derived and (references is None or references & (blocked | derived))
        }
        if not more:
            return derived
        derived |= more


def _included_entries(snapshot: dict, blanked_ids: set[str], *, turn_ids: set[str]) -> list[dict]:
    """Project the included records; any record the live policy now blocks keeps its id but loses its text."""
    included_ids = [value for value in snapshot["included_record_ids"] if isinstance(value, str)]
    messages = snapshot["messages"]
    current_turn_id = _current_turn_id(snapshot)
    entries: list[dict] = []
    for record_id, message in zip(included_ids, messages, strict=False):
        if not isinstance(message, dict) or message.get("role") == "system" or record_id == "system":
            continue
        # The current turn is the question, not retrieved history; the diff still reports its id.
        if record_id == current_turn_id:
            continue
        # A retraction quotes the hidden memory it is about; never project that text.
        if record_id.startswith(RETRACTION_PREFIX):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        # A real turn is history whatever its text says; only other records carry an envelope kind.
        kind = None if record_id in turn_ids else untrusted_data_kind(record_id, content)
        text = _included_text(record_id, content, kind)[:BLOCK_TEXT_LIMIT]
        withheld = record_id in blanked_ids
        if withheld:
            text = ""
        entries.append(
            {
                "id": record_id,
                "kind": kind or "history",
                "text": text,
                "trust": "data" if kind else "conversation",
                "withheld": withheld,
            }
        )
    return entries


def _quoted_records(snapshot: dict, turn_ids: set[str]) -> list[tuple[str, str]]:
    """``(id, full text)`` of every included record that is not a turn (tool results, memory envelopes)."""
    included_ids = [value for value in snapshot["included_record_ids"] if isinstance(value, str)]
    pairs: list[tuple[str, str]] = []
    for record_id, message in zip(included_ids, snapshot["messages"], strict=False):
        if record_id in turn_ids or record_id == "system" or record_id.startswith(RETRACTION_PREFIX):
            continue
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            pairs.append((record_id, content))
    return pairs


def _current_turn_id(snapshot: dict) -> str | None:
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, dict):
        return None
    turn_id = manifest.get("turn_id")
    return turn_id if isinstance(turn_id, str) else None


async def _excluded_entries(
    db: AsyncSession, *, owner_id: str, snapshot: dict, exclusions: set[str], inputs
) -> list[dict]:
    record_ids = sorted({value for value in snapshot.get("excluded_record_ids", []) if isinstance(value, str)})
    if not record_ids:
        return []
    rows = await _memory_rows_for_ids(db, owner_id, record_ids)
    status_by_id = {row.id: row.status for row in rows}
    sources = inputs.memories.blocked_sources
    entries = []
    for record_id in record_ids:
        status = status_by_id.get(record_id)
        if status == "hidden":
            reason = "hidden"
        elif status == "deleted":
            reason = "deleted"
        elif record_id in exclusions:
            reason = "excluded_in_conversation"
        elif record_id in sources:
            # Same words as the context lens (as of now): a restored memory falls back to "derived".
            reason = "source_of_blocked_memory"
        elif record_id in inputs.restating_ids:
            reason = "restates_forgotten_fact"
        else:
            reason = "derived"
        entries.append({"id": record_id, "reason": reason})
    return entries


def _included_record_ids(snapshot: dict) -> set[str]:
    return {value for value in snapshot.get("included_record_ids", []) if isinstance(value, str)}


def _diff(current: dict, previous: _SnapshotTurn | None) -> dict:
    current_ids = _included_record_ids(current)
    if previous is None:
        return {"added": sorted(current_ids), "removed": []}
    previous_ids = _included_record_ids(previous.snapshot)
    return {
        "added": sorted(current_ids - previous_ids),
        "removed": sorted(previous_ids - current_ids),
    }


def _projection_sha256(revision: int, included: list[dict], excluded: list[dict]) -> str:
    """A short digest of the receipt's own content, so two receipts can be told apart.

    It covers only what the receipt returns. A digest of the compiled prompt would let a caller confirm guesses of
    the system instructions and of redacted memory text, so the prompt's own hash is deliberately not exposed.
    """
    body = json.dumps(
        {"revision": revision, "included": included, "excluded": excluded}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def _used_chars(included: list[dict]) -> int:
    """Characters of the retrieved records shown (the instructions are not counted or revealed)."""
    return sum(len(entry["text"]) for entry in included)


async def get_turn_context_receipt(db: AsyncSession, *, owner_id: str, conversation_id: str, turn_id: str) -> dict:
    """Project the snapshot behind one turn, falling back to its preceding user turn."""
    await owned_conversation(db, owner_id, conversation_id)
    turn = await db.scalar(select(Turn).where(Turn.id == turn_id, Turn.conversation_id == conversation_id))
    if turn is None:
        raise HTTPException(404, detail={"code": "TURN_NOT_FOUND"})

    source = _snapshot_turn(turn)
    if source is None:
        source = await _user_snapshot_before(
            db, conversation_id=conversation_id, created_at=turn.created_at, turn_id=turn.id
        )
    if source is None:
        raise HTTPException(404, detail={"code": "TURN_HAS_NO_CONTEXT"})

    previous = await _user_snapshot_before(
        db, conversation_id=conversation_id, created_at=source.created_at, turn_id=source.id
    )
    snapshot = source.snapshot
    exclusions = await _active_exclusion_ids(db, owner_id, conversation_id)
    included_ids = _included_record_ids(snapshot)
    # The lens's own inputs (as of now): which turns are blocked or restate a forgotten fact.
    inputs = await read_context_inputs(db, owner_id=owner_id, conversation_id=conversation_id, combined_read=True)
    redacted_ids = await _redacted_memory_ids(db, owner_id=owner_id, record_ids=included_ids, exclusions=exclusions)
    turn_texts = await _turn_texts(db, conversation_id=conversation_id, record_ids=included_ids)
    # The lens window is bounded, so an old turn in this snapshot can fall outside it: test the snapshot's own
    # turns against every forgotten fact as well.
    # Tool results and other non-turn records are tested the same way: a weather result can quote a forgotten city.
    candidates = [*turn_texts.items(), *_quoted_records(snapshot, set(turn_texts))]
    restating = restating_turn_ids(candidates, (candidate.text for candidate in inputs.memories.denied))
    # Everything the live policy blocks now (lineage-blocked memories, blocked tool results, turns), whatever its kind.
    blanked_ids = redacted_ids | set(inputs.excluded_ids) | set(restating)
    blanked_ids |= await _derived_ids(
        db, conversation_id=conversation_id, included_ids=included_ids, blocked=blanked_ids
    )
    included = _included_entries(snapshot, blanked_ids, turn_ids=set(turn_texts))
    excluded = await _excluded_entries(db, owner_id=owner_id, snapshot=snapshot, exclusions=exclusions, inputs=inputs)
    revision = snapshot.get("revision")
    revision = revision if isinstance(revision, int) else 0
    return {
        "turn_id": turn.id,
        "revision": revision,
        "prompt_sha256": _projection_sha256(revision, included, excluded),
        "history_count": sum(1 for entry in included if entry["kind"] == "history"),
        "included": included,
        "excluded": excluded,
        "budget": {"used_chars": _used_chars(included), "limit_chars": DEFAULT_MAX_CHARS},
        "previous_turn_id": previous.id if previous is not None else None,
        "diff": _diff(snapshot, previous),
    }


__all__ = ["get_turn_context_receipt"]
