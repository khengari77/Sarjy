"""Authoritative context reader.

Reads the permitted turn history, active memories, active exclusions and
permitted tool results for one conversation revision. Persistence stays behind
this narrow seam: the default implementation uses the shared ORM, while tests
and the not-yet-merged #5 memory service can supply the same shapes through
``read_context_inputs``/``MemoryCandidates``.
"""
from dataclasses import dataclass
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context import ContextCandidate, canonical_json
from app.domain.weather_permissions import LocationEvidence
from app.infrastructure.database import ContextExclusion, Memory, ToolCall, Turn

HISTORY_LIMIT = 20
TOOL_RESULT_LIMIT = 50
DENIED_STATUSES = frozenset({'superseded', 'inactive', 'excluded'})


@dataclass(frozen=True)
class MemoryCandidates:
    """Permission-split memory view for one revision."""

    included: tuple[ContextCandidate, ...] = ()
    denied: tuple[ContextCandidate, ...] = ()

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

    @property
    def excluded_ids(self) -> frozenset[str]:
        return self.memories.excluded_ids

    def weather_policy(self) -> dict:
        """Location evidence frozen into the turn snapshot at this revision."""
        return {
            'included_memory_ids': [candidate.id for candidate in self.memories.included],
            'excluded_memory_ids': [candidate.id for candidate in self.memories.denied],
            'included_locations': [
                {'memory_id': evidence.memory_id, 'content': evidence.content}
                for evidence in self.memories.included_locations
            ],
            'excluded_locations': [
                {'memory_id': evidence.memory_id, 'content': evidence.content}
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
    excluded_rows = (
        await db.scalars(
            select(ContextExclusion).where(
                ContextExclusion.owner_id == owner_id,
                ContextExclusion.conversation_id == conversation_id,
                ContextExclusion.active.is_(True),
            )
        )
    ).all()
    excluded_ids = {row.memory_id for row in excluded_rows}
    rows = (await db.scalars(select(Memory).where(Memory.owner_id == owner_id).order_by(Memory.id))).all()
    included: list[ContextCandidate] = []
    denied: list[ContextCandidate] = []
    for row in rows:
        candidate = ContextCandidate(
            id=row.id,
            role='memory',
            text=row.content,
            origin='memory',
            source=row.source_conversation_id,
            status='active' if row.status == 'active' and row.id not in excluded_ids else 'superseded',
        )
        if candidate.status == 'active':
            included.append(candidate)
        else:
            denied.append(candidate)
    return MemoryCandidates(tuple(included), tuple(denied))


async def read_context_inputs(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    memory_reader: MemoryReader | None = None,
    history_limit: int = HISTORY_LIMIT,
) -> ContextInputs:
    """Read everything the compiler may consider for the next turn."""
    turns = (
        await db.scalars(
            select(Turn)
            .where(Turn.conversation_id == conversation_id, Turn.status == 'completed')
            .order_by(Turn.created_at.desc())
            .limit(history_limit)
        )
    ).all()
    history = tuple(
        ContextCandidate(id=turn.id, role=turn.role, text=turn.text, origin='history')
        for turn in reversed(turns)
    )

    tool_rows = (
        await db.execute(
            select(ToolCall, Turn)
            .join(Turn, ToolCall.turn_id == Turn.id)
            .where(Turn.conversation_id == conversation_id, Turn.status == 'completed')
            .order_by(Turn.created_at, ToolCall.id)
            .limit(TOOL_RESULT_LIMIT)
        )
    ).all()
    tool_results = tuple(
        ContextCandidate(
            id=call.id,
            role='tool',
            text=canonical_json(call.result),
            origin='tool',
            source=call.name,
        )
        for call, _turn in tool_rows
    )

    reader = memory_reader or _OrmMemoryReader()
    memories = await reader.candidates(db, owner_id=owner_id, conversation_id=conversation_id)
    return ContextInputs(history=history, memories=memories, tool_results=tool_results)


class _OrmMemoryReader:
    async def candidates(self, db: AsyncSession, *, owner_id: str, conversation_id: str) -> MemoryCandidates:
        return await orm_memory_candidates(db, owner_id=owner_id, conversation_id=conversation_id)


def location_evidence(entries: Sequence[dict]) -> tuple[LocationEvidence, ...]:
    """Rebuild weather evidence from a stored turn snapshot, never from live DB."""
    return tuple(
        LocationEvidence(memory_id=str(entry.get('memory_id', '')), content=str(entry.get('content', '')))
        for entry in entries
        if entry.get('memory_id') and entry.get('content')
    )


__all__ = [
    'ContextInputs', 'HISTORY_LIMIT', 'MemoryCandidates', 'MemoryReader',
    'TOOL_RESULT_LIMIT', 'location_evidence', 'orm_memory_candidates', 'read_context_inputs',
]
