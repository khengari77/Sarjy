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


async def _discover_closure_candidates(
    db: AsyncSession,
    *,
    conversation_id: str,
    chunk: list[str],
    remaining: int,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Discover candidate ids with scalar projections only, bounded by budget.

    No ORM instance is built here: turns contribute their id, tools contribute
    their id plus parent turn id (joined so a foreign-conversation tool can
    never leak into this closure).
    """
    turn_ids = list(
        (
            await db.scalars(
                select(Turn.id)
                .where(
                    Turn.conversation_id == conversation_id,
                    Turn.status == "completed",
                    Turn.id.in_(chunk),
                )
                .order_by(Turn.created_at, Turn.id)
                .limit(remaining)
            )
        ).all()
    )
    tool_pairs = [
        (tool_id, turn_id)
        for tool_id, turn_id in (
            await db.execute(
                select(ToolCall.id, ToolCall.turn_id)
                .join(Turn, ToolCall.turn_id == Turn.id)
                .where(
                    ToolCall.id.in_(chunk),
                    Turn.conversation_id == conversation_id,
                    Turn.status == "completed",
                )
                .order_by(ToolCall.id)
                .limit(remaining)
            )
        ).all()
    ]
    return turn_ids, tool_pairs


def _admit_closure_candidates(
    turn_ids: list[str],
    tool_pairs: list[tuple[str, str]],
    *,
    loaded: dict[str, Turn],
    tools: dict[str, tuple[ToolCall, Turn]],
    remaining: int,
) -> tuple[list[str], list[str], list[str], int, bool]:
    """Admit candidates in deterministic order while cost fits the budget.

    Every distinct new turn and tool costs one.  A tool's parent turn costs one
    only when it is neither already loaded nor admitted by an earlier tool in
    this page, so shared parents are charged once regardless of tool count.
    """
    admitted_turns: list[str] = []
    admitted_tools: list[str] = []
    admitted_parents: list[str] = []
    admitted_turn_set = set(loaded)
    cost = 0
    exceeded = False
    for turn_id in turn_ids:
        if turn_id in loaded:
            continue
        if cost + 1 > remaining:
            exceeded = True
            break
        cost += 1
        admitted_turns.append(turn_id)
        admitted_turn_set.add(turn_id)
    if not exceeded:
        for tool_id, parent_id in tool_pairs:
            if tool_id in tools:
                continue
            tool_cost = 1
            parent_is_new = parent_id not in admitted_turn_set
            if parent_is_new:
                tool_cost += 1
            if cost + tool_cost > remaining:
                exceeded = True
                break
            cost += tool_cost
            admitted_tools.append(tool_id)
            if parent_is_new:
                admitted_turn_set.add(parent_id)
                admitted_parents.append(parent_id)
    return admitted_turns, admitted_tools, admitted_parents, cost, exceeded


async def _closure_page(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    chunk: list[str],
    loaded: dict[str, Turn],
    tools: dict[str, tuple[ToolCall, Turn]],
    frontier: set[str],
    budget: int,
    fetched: int,
) -> tuple[bool, int]:
    """Resolve one id chunk of turns, tool provenance and memory references.

    Candidate ids are discovered with scalar projections first so a large
    frontier cannot materialize ORM rows before the budget is charged.  Turns
    and tool provenance then share one closure budget: every distinct new turn
    costs one, every distinct new tool costs one, and a tool's parent turn costs
    one only when it is neither loaded nor already admitted in this page.  Cost
    is charged before any full row is loaded, so unadmitted provenance is never
    materialized; if the required references cannot fit, the page reports
    ``exceeded`` and the caller fails closed.
    """
    if fetched >= budget:
        return True, fetched
    remaining = budget - fetched
    turn_ids, tool_pairs = await _discover_closure_candidates(
        db, conversation_id=conversation_id, chunk=chunk, remaining=remaining
    )
    admitted_turns, admitted_tools, admitted_parents, cost, exceeded = _admit_closure_candidates(
        turn_ids, tool_pairs, loaded=loaded, tools=tools, remaining=remaining
    )

    # Referenced ids may legitimately be memory records or the synthetic
    # instruction id. Anything else is unresolved provenance: fail closed.  This
    # also catches ids omitted by the bounded discovery queries before any full
    # row for them is loaded.
    resolved = set(turn_ids) | {tool_id for tool_id, _ in tool_pairs} | set(tools) | {"system"}
    unresolved = set(chunk) - resolved
    if unresolved:
        memory_ids = set(
            (await db.scalars(select(Memory.id).where(Memory.owner_id == owner_id, Memory.id.in_(unresolved)))).all()
        )
        if unresolved - memory_ids:
            exceeded = True

    if admitted_turns or admitted_parents:
        load_ids = sorted(set(admitted_turns) | set(admitted_parents))
        rows = (
            await db.scalars(
                select(Turn)
                .where(
                    Turn.conversation_id == conversation_id,
                    Turn.status == "completed",
                    Turn.id.in_(load_ids),
                )
                .order_by(Turn.created_at, Turn.id)
            )
        ).all()
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(load_ids):
            return True, fetched
        for row in rows:
            loaded[row.id] = row
            frontier.update(_dependency_ids(row))

    if admitted_tools:
        call_rows = (
            await db.scalars(select(ToolCall).where(ToolCall.id.in_(admitted_tools)).order_by(ToolCall.id))
        ).all()
        by_id = {call.id: call for call in call_rows}
        if len(by_id) != len(admitted_tools):
            return True, fetched
        for call in call_rows:
            parent = loaded.get(call.turn_id)
            if parent is None:
                return True, fetched
            tools[call.id] = (call, parent)

    return exceeded, fetched + cost


async def _dependency_closure(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    loaded: dict[str, Turn],
    budget: int = DEPENDENCY_CLOSURE_LIMIT,
) -> tuple[bool, dict[str, tuple[ToolCall, Turn]]]:
    """Load referenced turns and tool provenance needed to propagate exclusions.

    Tool ids referenced by a snapshot are resolved by exact id even when the
    provider tool-result window no longer contains them.  Returns ``True`` when
    the budget cannot cover every reference or provenance cannot be resolved;
    the caller must then fail closed for derived content while exclusions exist.
    """
    attempted = set(loaded)
    tools: dict[str, tuple[ToolCall, Turn]] = {}
    frontier: set[str] = set()
    for turn in loaded.values():
        frontier.update(_dependency_ids(turn))
    fetched = 0
    while True:
        missing = sorted(frontier - attempted)
        if not missing:
            return False, tools
        for start in range(0, len(missing), _ID_CHUNK):
            chunk = missing[start : start + _ID_CHUNK]
            attempted.update(chunk)
            exceeded, fetched = await _closure_page(
                db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                chunk=chunk,
                loaded=loaded,
                tools=tools,
                frontier=frontier,
                budget=budget,
                fetched=fetched,
            )
            if exceeded:
                return True, tools


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


def _known_tool_dependencies(turn: Turn) -> bool:
    values = (turn.snapshot or {}).get("included_record_ids")
    return isinstance(values, list) and all(isinstance(value, str) for value in values)


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
    closure_tools: dict[str, tuple[ToolCall, Turn]] = {}
    if policy_active:
        # Tool rows are bounded by the tool-result limit, not the history window.
        # Their parents must join the evaluated set so an exclusion can propagate
        # through older derivations; no traversal happens with no active policy.
        for _, parent in tool_rows:
            by_id.setdefault(parent.id, parent)
        closure_exceeded, closure_tools = await _dependency_closure(
            db, owner_id=owner_id, conversation_id=conversation_id, loaded=by_id
        )
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
    blocked_tools: set[str] = set()
    tool_dependencies: dict[str, frozenset[str]] = {}
    window_tool_ids = {call.id for call, _ in tool_rows}
    all_tools = list(tool_rows) + [pair for tool_id, pair in closure_tools.items() if tool_id not in window_tool_ids]
    for call, turn in all_tools:
        values = (turn.snapshot or {}).get("included_record_ids")
        known = isinstance(values, list) and all(isinstance(value, str) for value in values)
        tool_dependencies[call.id] = frozenset(values) if known else frozenset()
    # Assistant replies and tool results must reach one joint fixpoint: blocking a
    # tool result after the assistant loop has finished lets a reply derived from
    # that tool result escape. Iterate both until neither set grows.
    while True:
        active = blocked | blocked_tools
        more = {
            turn.id
            for turn in turns
            if turn.role == "assistant"
            and (
                fail_closed
                or (dependencies(turn) is not None and dependencies(turn) & active)
                or (policy_active and dependencies(turn) is None)
            )
        }
        more_tools = {
            call.id
            for call, turn in all_tools
            if (
                fail_closed
                or (policy_active and turn.id not in by_id)
                or turn.id in active
                or tool_dependencies[call.id] & active
                or (policy_active and not _known_tool_dependencies(turn))
            )
        }
        if more <= blocked and more_tools <= blocked_tools:
            break
        blocked.update(more)
        blocked_tools.update(more_tools)
    history = tuple(
        ContextCandidate(id=turn.id, role=turn.role, text=turn.text, origin="history")
        for turn in window[-history_limit:]
        if turn.id not in blocked
    )
    tool_results = tuple(
        ContextCandidate(
            id=call.id,
            role="tool",
            text=canonical_json(call.result),
            origin="tool",
            source=call.name,
            dependencies=tool_dependencies[call.id],
        )
        for call, _ in tool_rows
        if call.id not in blocked_tools
    )
    return ContextInputs(
        history=history,
        memories=memories,
        tool_results=tool_results,
        blocked_ids=frozenset(blocked | blocked_tools),
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
