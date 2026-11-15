"""Read-only per-conversation context block listing for the next turn.

Every block the assembler considers is listed with its state.  The inputs come
from the same ``read_context_inputs`` the worker uses and inclusion is decided by
the same ``compile_turn_context``; nothing here applies a second policy and
nothing is written.  System instructions are never part of the listing.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context import canonical_json, compile_turn_context
from app.infrastructure.database import ContextExclusion, ToolCall, Turn
from app.services.authorization import owned_conversation
from app.services.context_assembly import (
    HISTORY_LIMIT,
    HISTORY_WINDOW_MULTIPLIER,
    _memory_rows_for_ids,
    _recent_turns,
    read_context_inputs,
)

BLOCK_TEXT_LIMIT = 400
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
TOKEN_ESTIMATE_METHOD = "estimate: characters / 4"
# Stand-in for the (private) system instructions; only its presence matters to the budget.
_INSTRUCTIONS_PLACEHOLDER = "-"


@dataclass(frozen=True)
class _Block:
    id: str
    kind: str
    role: str
    text: str
    state: str
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "role": self.role,
            "text": self.text[:BLOCK_TEXT_LIMIT],
            "truncated": len(self.text) > BLOCK_TEXT_LIMIT,
            "estimated_tokens": max(1, len(self.text) // 4) if self.text else 0,
            "state": self.state,
            "reason": self.reason,
        }


async def _active_exclusion_ids(db: AsyncSession, owner_id: str, conversation_id: str) -> set[str]:
    return set(
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


async def _blocked_tool_blocks(db: AsyncSession, conversation_id: str, ids: set[str]) -> list[_Block]:
    if not ids:
        return []
    rows = (
        await db.scalars(
            select(ToolCall)
            .join(Turn, ToolCall.turn_id == Turn.id)
            .where(Turn.conversation_id == conversation_id, ToolCall.id.in_(sorted(ids)))
            .order_by(ToolCall.id)
        )
    ).all()
    return [
        _Block(row.id, "tool_result", "tool", canonical_json(row.result), "blocked", "derived_from_blocked_record")
        for row in rows
    ]


async def _memory_blocks(db, *, owner_id, conversation_id, inputs, sent: set[str]) -> list[_Block]:
    excluded = await _active_exclusion_ids(db, owner_id, conversation_id)
    blocks = [
        _Block(
            c.id,
            "memory",
            "memory",
            c.text,
            "included" if c.id in sent else "not_sent",
            None if c.id in sent else "budget",
        )
        for c in inputs.memories.included
    ]
    denied_ids = {c.id for c in inputs.memories.denied}
    status_by_id = {row.id: row.status for row in await _memory_rows_for_ids(db, owner_id, denied_ids)}
    for candidate in inputs.memories.denied:
        status = status_by_id.get(candidate.id)
        if status in ("hidden", "deleted"):
            blocks.append(_Block(candidate.id, "memory", "memory", candidate.text, "blocked", status))
        elif candidate.id in excluded:
            blocks.append(
                _Block(candidate.id, "memory", "memory", candidate.text, "excluded", "conversation_exclusion")
            )
        else:
            blocks.append(_Block(candidate.id, "memory", "memory", candidate.text, "blocked", "lineage"))
    return blocks


def _turn_blocks(window: list[Turn], inputs, sent: set[str]) -> list[_Block]:
    history_ids = {c.id for c in inputs.history}
    sources = inputs.memories.blocked_sources
    blocks = []
    for turn in window:
        if turn.id in inputs.blocked_ids:
            reason = "source_of_blocked_memory" if turn.id in sources else "derived_from_blocked_record"
            blocks.append(_Block(turn.id, "turn", turn.role, turn.text, "blocked", reason))
        elif turn.id in history_ids:
            state, reason = ("included", None) if turn.id in sent else ("not_sent", "budget")
            blocks.append(_Block(turn.id, "turn", turn.role, turn.text, state, reason))
        else:
            blocks.append(_Block(turn.id, "turn", turn.role, turn.text, "outside_window", "older_than_history_limit"))
    return blocks


def _tool_blocks(inputs, sent: set[str]) -> list[_Block]:
    return [
        _Block(
            c.id,
            "tool_result",
            "tool",
            c.text,
            "included" if c.id in sent else "not_sent",
            None if c.id in sent else "budget_or_tool_policy",
        )
        for c in inputs.tool_results
    ]


async def list_context_blocks(
    db: AsyncSession, *, owner_id: str, conversation_id: str, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> dict:
    conversation = await owned_conversation(db, owner_id, conversation_id)
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    inputs = await read_context_inputs(db, owner_id=owner_id, conversation_id=conversation_id)
    compiled = compile_turn_context(
        revision=conversation.revision,
        instructions=_INSTRUCTIONS_PLACEHOLDER,
        history=inputs.history,
        memories=(*inputs.memories.included, *inputs.retractions),
        tool_results=inputs.tool_results,
        excluded_ids=inputs.excluded_ids,
    )
    sent = set(compiled.included_ids)
    window = await _recent_turns(db, conversation_id, limit=HISTORY_LIMIT * HISTORY_WINDOW_MULTIPLIER)
    listed_ids = {c.id for c in inputs.history} | {t.id for t in window}
    tool_ids = {c.id for c in inputs.tool_results}
    memory_ids = {c.id for c in inputs.memories.included} | {c.id for c in inputs.memories.denied}
    blocked_tool_ids = set(inputs.blocked_ids) - listed_ids - memory_ids - tool_ids

    retraction = [
        _Block(
            c.id,
            "retraction",
            "memory",
            c.text,
            "included" if c.id in sent else "not_sent",
            None if c.id in sent else "budget",
        )
        for c in inputs.retractions
    ]
    blocks = [
        *retraction,
        *await _memory_blocks(db, owner_id=owner_id, conversation_id=conversation_id, inputs=inputs, sent=sent),
        *_turn_blocks(window, inputs, sent),
        *_tool_blocks(inputs, sent),
        *await _blocked_tool_blocks(db, conversation_id, blocked_tool_ids),
    ]
    counts = {state: 0 for state in ("included", "excluded", "blocked", "outside_window", "not_sent")}
    included_tokens = 0
    for block in blocks:
        counts[block.state] += 1
        if block.state == "included":
            included_tokens += max(1, len(block.text) // 4) if block.text else 0
    page = blocks[offset : offset + limit]
    return {
        "conversation_id": conversation_id,
        "revision": conversation.revision,
        "token_estimate_method": TOKEN_ESTIMATE_METHOD,
        "included_tokens_estimate": included_tokens,
        "counts": {**counts, "total": len(blocks)},
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(blocks),
        "blocks": [block.as_dict() for block in page],
    }


__all__ = ["list_context_blocks"]
