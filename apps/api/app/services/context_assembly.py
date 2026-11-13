"""Authoritative context reader.

Reads the permitted turn history, active memories, active exclusions and
permitted tool results for one conversation revision. Persistence stays behind
this narrow seam: the default implementation uses the shared ORM, while tests
and alternate memory services can supply the same shapes through
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
# The model still receives at most ``HISTORY_LIMIT`` turns, but retrieval reads
# a small, deterministic recent window.  Older turns are loaded only when a
# dependency needs their provenance to propagate an exclusion.
HISTORY_WINDOW_MULTIPLIER = 5
DEPENDENCY_CLOSURE_LIMIT = 200
# Bounded retrieval considers only the newest active memories for inclusion.
# Older active memories are intentionally outside this revision's candidate
# set; exclusions and correction lineage remain authoritative by exact id.
MEMORY_CANDIDATE_LIMIT = 200
TOOL_RESULT_LIMIT = 50
_ID_CHUNK = 400
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
    """Default reader: owner-scoped memories with active conversation exclusions.

    Only the deterministic candidate bound is materialized for inclusion.  Rows
    for excluded ids are fetched by id because their content is policy evidence
    (notably excluded locations for the weather boundary), never as a lifetime
    owner-memory scan.  Inactive rows play no part in this revision and are not
    loaded.
    """
    excluded_ids = await blocked_memory_ids(db, owner_id, conversation_id)
    sources = await blocked_source_turn_ids(db, owner_id, conversation_id)
    rows = (
        await db.scalars(
            select(Memory)
            .where(Memory.owner_id == owner_id, Memory.status == "active")
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(MEMORY_CANDIDATE_LIMIT)
        )
    ).all()
    active_by_id = {row.id: row for row in rows}
    denied_by_id = {row.id: row for row in await _memory_rows_for_ids(db, owner_id, excluded_ids - set(active_by_id))}
    included: list[ContextCandidate] = [
        ContextCandidate(
            id=row.id,
            role="memory",
            text=row.content,
            origin="memory",
            source=row.source_turn_id,
            status="active",
        )
        for row in sorted(rows, key=lambda item: item.id)
        if row.id not in excluded_ids
    ]
    denied: list[ContextCandidate] = []
    for memory_id in sorted(excluded_ids):
        row = active_by_id.get(memory_id) or denied_by_id.get(memory_id)
        if row is None:
            continue
        denied.append(
            ContextCandidate(
                id=row.id,
                role="memory",
                text=row.content,
                origin="memory",
                source=row.source_turn_id,
                status="superseded",
            )
        )
    return MemoryCandidates(tuple(included), tuple(denied), (), sources)


async def _memory_rows_for_ids(db: AsyncSession, owner_id: str, ids) -> list[Memory]:
    """Fetch policy rows by exact owner-scoped id without a lifetime scan."""
    wanted = sorted(str(identifier) for identifier in ids)
    rows: list[Memory] = []
    for start in range(0, len(wanted), _ID_CHUNK):
        chunk = wanted[start : start + _ID_CHUNK]
        rows.extend(
            (
                await db.scalars(
                    select(Memory)
                    .where(Memory.owner_id == owner_id, Memory.id.in_(chunk))
                    .order_by(Memory.updated_at.desc(), Memory.id.desc())
                )
            ).all()
        )
    return rows


def _dependency_ids(turn: Turn) -> set[str]:
    """Record ids one turn can inherit dependencies from."""
    references: set[str] = set()
    if turn.request_id.startswith("reply:"):
        references.add(turn.request_id.removeprefix("reply:"))
    snapshot = turn.snapshot or {}
    derivation = snapshot.get("derivation")
    values = derivation.get("record_ids") if isinstance(derivation, dict) else snapshot.get("included_record_ids")
    if isinstance(values, list):
        references.update(value for value in values if isinstance(value, str))
    return references


async def _dependency_closure(
    db: AsyncSession,
    *,
    conversation_id: str,
    loaded: dict[str, Turn],
    budget: int = DEPENDENCY_CLOSURE_LIMIT,
) -> bool:
    """Load only referenced turn ids needed to propagate exclusions.

    Returns ``True`` when the budget cannot cover every referenced turn.  The
    caller must then fail closed for derived content while exclusions exist.
    """
    attempted = set(loaded)
    frontier: set[str] = set()
    for turn in loaded.values():
        frontier.update(_dependency_ids(turn))
    fetched = 0
    while True:
        missing = sorted(frontier - attempted)
        if not missing:
            return False
        for start in range(0, len(missing), _ID_CHUNK):
            chunk = missing[start : start + _ID_CHUNK]
            attempted.update(chunk)
            remaining = budget - fetched
            if remaining <= 0:
                return True
            rows = (
                await db.scalars(
                    select(Turn)
                    .where(
                        Turn.conversation_id == conversation_id,
                        Turn.status == "completed",
                        Turn.id.in_(chunk),
                    )
                    .order_by(Turn.created_at, Turn.id)
                )
            ).all()
            if len(rows) > remaining:
                rows = rows[:remaining]
                for row in rows:
                    loaded[row.id] = row
                return True
            for row in rows:
                if row.id not in loaded:
                    loaded[row.id] = row
                    fetched += 1
                    frontier.update(_dependency_ids(row))


async def _recent_turns(db: AsyncSession, conversation_id: str, *, limit: int) -> list[Turn]:
    """Deterministic recent window, returned in chronological model order."""
    rows = (
        await db.scalars(
            select(Turn)
            .where(Turn.conversation_id == conversation_id, Turn.status == "completed")
            .order_by(Turn.created_at.desc(), Turn.id.desc())
            .limit(limit)
        )
    ).all()
    return list(reversed(rows))


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
    policy_active = bool(memories.excluded_ids or memories.blocked_sources)
    tool_rows = (
        await db.execute(
            select(ToolCall, Turn)
            .join(Turn, ToolCall.turn_id == Turn.id)
            .where(
                Turn.conversation_id == conversation_id,
                Turn.status == "completed",
                ToolCall.state == "completed",
            )
            .order_by(Turn.created_at.desc(), ToolCall.id)
            .limit(TOOL_RESULT_LIMIT)
        )
    ).all()
    window = await _recent_turns(db, conversation_id, limit=history_limit * HISTORY_WINDOW_MULTIPLIER)
    by_id = {turn.id: turn for turn in window}
    closure_exceeded = False
    if policy_active:
        # Tool rows are bounded by the tool-result limit, not the history window.
        # Their parents must join the evaluated set so an exclusion can propagate
        # through older derivations; no traversal happens with no active policy.
        for _, parent in tool_rows:
            by_id.setdefault(parent.id, parent)
        closure_exceeded = await _dependency_closure(db, conversation_id=conversation_id, loaded=by_id)
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
    # Unknown provenance or an exhausted closure fails closed while exclusions exist.
    fail_closed = closure_exceeded and policy_active
    turns = list(by_id.values())
    while True:
        more = {
            turn.id
            for turn in turns
            if turn.role == "assistant"
            and (
                fail_closed
                or (dependencies(turn) is not None and dependencies(turn) & blocked)
                or (policy_active and dependencies(turn) is None)
            )
        }
        if more <= blocked:
            break
        blocked.update(more)
    history = tuple(
        ContextCandidate(id=turn.id, role=turn.role, text=turn.text, origin="history")
        for turn in window[-history_limit:]
        if turn.id not in blocked
    )
    tool_results = []
    for call, turn in tool_rows:
        values = (turn.snapshot or {}).get("included_record_ids")
        known = isinstance(values, list) and all(isinstance(value, str) for value in values)
        deps = frozenset(values) if known else frozenset()
        # An out-of-window tool parent is unknown only while an exclusion policy
        # is active; with no exclusions the base permitted it and must stay admitted.
        if (
            fail_closed
            or (policy_active and turn.id not in by_id)
            or turn.id in blocked
            or deps & blocked
            or (policy_active and not known)
        ):
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
    "DEPENDENCY_CLOSURE_LIMIT",
    "HISTORY_LIMIT",
    "HISTORY_WINDOW_MULTIPLIER",
    "MEMORY_CANDIDATE_LIMIT",
    "MemoryCandidates",
    "MemoryReader",
    "TOOL_RESULT_LIMIT",
    "location_evidence",
    "orm_memory_candidates",
    "read_context_inputs",
]
