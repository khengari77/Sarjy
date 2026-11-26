"""Read-only, user-safe projection of one turn's stored context snapshot.

The snapshot is the authoritative record of what the compiler gave the model.
This service exposes only the parts a user may see: conversation text, memory
and tool data, excluded record ids with a reason, and a budget summary. System
instructions, the pipeline configuration, the provider request and excluded
content are never read back from the snapshot.
"""

import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context import (
    DEFAULT_MAX_CHARS,
    untrusted_data_kind,
    untrusted_data_prefix,
)
from app.infrastructure.database import Turn
from app.services.authorization import owned_conversation
from app.services.context_assembly import RETRACTION_PREFIX, _memory_rows_for_ids, read_context_inputs
from app.services.context_blocks import BLOCK_TEXT_LIMIT, _active_exclusion_ids


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
    db: AsyncSession, *, owner_id: str, conversation_id: str, record_ids: set[str]
) -> set[str]:
    """Included memories that are now hidden, deleted or excluded in this conversation."""
    rows = await _memory_rows_for_ids(db, owner_id, record_ids)
    redacted = {row.id for row in rows if row.status in ("hidden", "deleted")}
    return redacted | (await _active_exclusion_ids(db, owner_id, conversation_id))


def _included_entries(snapshot: dict, redacted_ids: set[str]) -> list[dict]:
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
        kind = untrusted_data_kind(record_id, content)
        text = _included_text(record_id, content, kind)[:BLOCK_TEXT_LIMIT]
        if kind == "memory" and record_id in redacted_ids:
            text = ""
        entries.append(
            {
                "id": record_id,
                "kind": kind or "history",
                "text": text,
                "trust": "data" if kind else "conversation",
            }
        )
    return entries


def _current_turn_id(snapshot: dict) -> str | None:
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, dict):
        return None
    turn_id = manifest.get("turn_id")
    return turn_id if isinstance(turn_id, str) else None


async def _excluded_entries(db: AsyncSession, *, owner_id: str, conversation_id: str, snapshot: dict) -> list[dict]:
    record_ids = sorted({value for value in snapshot.get("excluded_record_ids", []) if isinstance(value, str)})
    if not record_ids:
        return []
    rows = await _memory_rows_for_ids(db, owner_id, record_ids)
    status_by_id = {row.id: row.status for row in rows}
    active_exclusions = await _active_exclusion_ids(db, owner_id, conversation_id)
    turn_reasons = await _turn_reasons(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        turn_ids=[i for i in record_ids if i not in status_by_id],
    )
    entries = []
    for record_id in record_ids:
        status = status_by_id.get(record_id)
        if status == "hidden":
            reason = "hidden"
        elif status == "deleted":
            reason = "deleted"
        elif record_id in active_exclusions:
            reason = "excluded_in_conversation"
        else:
            reason = turn_reasons.get(record_id, "derived")
        entries.append({"id": record_id, "reason": reason})
    return entries


async def _turn_reasons(
    db: AsyncSession, *, owner_id: str, conversation_id: str, turn_ids: list[str]
) -> dict[str, str]:
    """Why an excluded turn was dropped, in the same words as the context lens (as of now).

    A turn that was the source of a blocked memory, or that restates a forgotten fact, says so; anything else
    (including a record whose memory has since been restored) stays the generic ``derived``.
    """
    if not turn_ids:
        return {}
    inputs = await read_context_inputs(db, owner_id=owner_id, conversation_id=conversation_id, combined_read=True)
    sources = inputs.memories.blocked_sources
    reasons: dict[str, str] = {}
    for turn_id in turn_ids:
        if turn_id in sources:
            reasons[turn_id] = "source_of_blocked_memory"
        elif turn_id in inputs.restating_ids:
            reasons[turn_id] = "restates_forgotten_fact"
    return reasons


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


def _prompt_sha256(snapshot: dict) -> str:
    digest = snapshot.get("canonical_prompt_sha256")
    if not isinstance(digest, str):
        manifest = snapshot.get("manifest")
        digest = manifest.get("canonical_prompt_sha256") if isinstance(manifest, dict) else None
    return digest[:12] if isinstance(digest, str) else ""


def _used_chars(snapshot: dict) -> int:
    return sum(
        len(message["content"])
        for message in snapshot["messages"]
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    )


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
    redacted_ids = await _redacted_memory_ids(
        db, owner_id=owner_id, conversation_id=conversation_id, record_ids=_included_record_ids(snapshot)
    )
    included = _included_entries(snapshot, redacted_ids)
    revision = snapshot.get("revision")
    return {
        "turn_id": turn.id,
        "revision": revision if isinstance(revision, int) else 0,
        "prompt_sha256": _prompt_sha256(snapshot),
        "history_count": sum(1 for entry in included if entry["kind"] == "history"),
        "included": included,
        "excluded": await _excluded_entries(db, owner_id=owner_id, conversation_id=conversation_id, snapshot=snapshot),
        "budget": {"used_chars": _used_chars(snapshot), "limit_chars": DEFAULT_MAX_CHARS},
        "previous_turn_id": previous.id if previous is not None else None,
        "diff": _diff(snapshot, previous),
    }


__all__ = ["get_turn_context_receipt"]
