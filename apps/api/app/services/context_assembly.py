"""Authoritative context reader.

Reads the permitted turn history, active memories, active exclusions and
permitted tool results for one conversation revision. Persistence stays behind
this narrow seam: the default implementation uses the shared ORM, while tests
and the not-yet-merged #5 memory service can supply the same shapes through
``read_context_inputs``/``MemoryCandidates``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context import ContextCandidate, canonical_json
from app.domain.weather_permissions import LocationEvidence
from app.infrastructure.database import Memory, ToolCall, Turn
from app.services.memory_eligibility import blocked_memory_ids, blocked_source_turn_ids

HISTORY_LIMIT = 20
TOOL_RESULT_LIMIT = 50
DENIED_STATUSES = frozenset({"superseded", "inactive", "excluded"})


@dataclass(frozen=True)
class MemoryCandidates:
    """Permission-split memory view for one revision."""

    included: tuple[ContextCandidate, ...] = ()
    denied: tuple[ContextCandidate, ...] = ()
    inactive: tuple[ContextCandidate, ...] = ()
    blocked_sources: frozenset[str] = frozenset()

    @property
    def excluded_ids(self) -> frozenset[str]:
        return frozenset(candidate.id for candidate in self.denied)

    @property
    def included_locations(self) -> tuple[LocationEvidence, ...]:
        return tuple(LocationEvidence(candidate.id, candidate.text) for candidate in self.included)

    @property
    def denied_locations(self) -> tuple[LocationEvidence, ...]:
        return tuple(LocationEvidence(candidate.id, candidate.text) for candidate in self.denied)


class MemoryReader(Protocol):
    """Narrow adapter seam for the #5 memory service while it is in flight."""

    async def candidates(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        conversation_id: str,
    ) -> MemoryCandidates: ...


@dataclass(frozen=True)
class ContextInputs:
    history: tuple[ContextCandidate, ...]
    memories: MemoryCandidates
    tool_results: tuple[ContextCandidate, ...]
    blocked_ids: frozenset[str] = frozenset()

    @property
    def excluded_ids(self) -> frozenset[str]:
        return self.memories.excluded_ids | self.blocked_ids

    def weather_policy(self) -> dict:
        """Location evidence frozen into the turn snapshot at this revision."""
        return {
            "included_memory_ids": [candidate.id for candidate in self.memories.included],
            "excluded_memory_ids": [candidate.id for candidate in self.memories.denied],
            "included_locations": [
                {"memory_id": evidence.memory_id, "content": evidence.content}
                for evidence in self.memories.included_locations
            ],
            "excluded_locations": [
                {"memory_id": evidence.memory_id, "content": evidence.content}
                for evidence in self.memories.denied_locations
            ],
        }


async def orm_memory_candidates(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
) -> MemoryCandidates:
    """Default reader: owner-scoped memories with active conversation exclusions."""
    excluded_ids = await blocked_memory_ids(db, owner_id, conversation_id)
    sources = await blocked_source_turn_ids(db, owner_id, conversation_id)
    rows = (await db.scalars(select(Memory).where(Memory.owner_id == owner_id).order_by(Memory.id))).all()
    included: list[ContextCandidate] = []
    denied: list[ContextCandidate] = []
    inactive: list[ContextCandidate] = []
    for row in rows:
        candidate = ContextCandidate(
            id=row.id,
            role="memory",
            text=row.content,
            origin="memory",
            source=row.source_turn_id,
            status="active" if row.status == "active" and row.id not in excluded_ids else "superseded",
        )
        if candidate.status == "active":
            included.append(candidate)
        elif row.id in excluded_ids:
            denied.append(candidate)
        else:
            inactive.append(candidate)
    return MemoryCandidates(tuple(included), tuple(denied), tuple(inactive), sources)


async def read_context_inputs(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    memory_reader: MemoryReader | None = None,
    history_limit: int = HISTORY_LIMIT,
) -> ContextInputs:
    """Read everything the compiler may consider for the next turn."""
    reader = memory_reader or _OrmMemoryReader()
    memories = await reader.candidates(db, owner_id=owner_id, conversation_id=conversation_id)
    turns = (
        await db.scalars(
            select(Turn)
            .where(Turn.conversation_id == conversation_id, Turn.status == "completed")
            .order_by(Turn.created_at, Turn.id)
        )
    ).all()
    by_id = {turn.id: turn for turn in turns}
    blocked = set(memories.excluded_ids | memories.blocked_sources)

    def dependencies(turn):
        snapshot = turn.snapshot or {}
        derivation = snapshot.get("derivation")
        if isinstance(derivation, dict):
            values = derivation.get("record_ids")
        else:
            parent = by_id.get(turn.request_id.removeprefix("reply:")) if turn.request_id.startswith("reply:") else None
            values = (parent.snapshot or {}).get("included_record_ids") if parent else None
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return None
        return frozenset(values)

    # Only generated assistant content inherits model-input dependencies. Independent
    # user history is retained unless it is the explicitly tracked source of a memory.
    while True:
        more = {
            turn.id
            for turn in turns
            if turn.role == "assistant"
            and (
                (dependencies(turn) is not None and dependencies(turn) & blocked)
                or (memories.excluded_ids and dependencies(turn) is None)
            )
        }
        if more <= blocked:
            break
        blocked.update(more)
    history = tuple(
        ContextCandidate(id=turn.id, role=turn.role, text=turn.text, origin="history")
        for turn in turns[-history_limit:]
        if turn.id not in blocked
    )
    tool_rows = (
        await db.execute(
            select(ToolCall, Turn)
            .join(Turn, ToolCall.turn_id == Turn.id)
            .where(Turn.conversation_id == conversation_id, Turn.status == "completed")
            .order_by(Turn.created_at.desc(), ToolCall.id)
            .limit(TOOL_RESULT_LIMIT)
        )
    ).all()
    tool_results = []
    for call, turn in tool_rows:
        values = (turn.snapshot or {}).get("included_record_ids")
        known = isinstance(values, list) and all(isinstance(value, str) for value in values)
        deps = frozenset(values) if known else frozenset()
        if turn.id in blocked or deps & blocked or (memories.excluded_ids and not known):
            blocked.add(call.id)
            continue
        tool_results.append(
            ContextCandidate(
                id=call.id,
                role="tool",
                text=canonical_json(call.result),
                origin="tool",
                source=call.name,
                dependencies=deps,
            )
        )
    return ContextInputs(
        history=history, memories=memories, tool_results=tuple(tool_results), blocked_ids=frozenset(blocked)
    )


class _OrmMemoryReader:
    async def candidates(self, db: AsyncSession, *, owner_id: str, conversation_id: str) -> MemoryCandidates:
        return await orm_memory_candidates(db, owner_id=owner_id, conversation_id=conversation_id)


def location_evidence(entries: Sequence[dict]) -> tuple[LocationEvidence, ...]:
    """Rebuild weather evidence from a stored turn snapshot, never from live DB."""
    return tuple(
        LocationEvidence(memory_id=str(entry.get("memory_id", "")), content=str(entry.get("content", "")))
        for entry in entries
        if entry.get("memory_id") and entry.get("content")
    )


__all__ = [
    "ContextInputs",
    "HISTORY_LIMIT",
    "MemoryCandidates",
    "MemoryReader",
    "TOOL_RESULT_LIMIT",
    "location_evidence",
    "orm_memory_candidates",
    "read_context_inputs",
]
