"""Authoritative context reader.

Reads the permitted turn history, active memories, active exclusions and
permitted tool results for one conversation revision. Persistence stays behind
this narrow seam: the default implementation uses the shared ORM, while tests
and alternate memory services can supply the same shapes through
``read_context_inputs``/``MemoryCandidates``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import cast, inspect, literal, null, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.domain.context import ContextCandidate, canonical_json
from app.domain.restatement import restating_turn_ids
from app.domain.weather_permissions import LocationEvidence
from app.infrastructure.database import Memory, ToolCall, Turn
from app.services.memory_eligibility import blocked_memory_ids_select, blocked_memory_rows

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
# Explicit column lists for the one-statement turn-start read.  The union parts
# share this order; ``snapshot`` and the deferred ``embedding`` are deliberately
# absent so neither can be selected by the combined read.
_TURN_READ_COLUMNS = (
    Turn.id,
    Turn.conversation_id,
    Turn.request_id,
    Turn.role,
    Turn.text,
    Turn.status,
    Turn.created_at,
)
_MEMORY_READ_COLUMNS = (
    Memory.id,
    Memory.owner_id,
    Memory.subject,
    Memory.content,
    Memory.embedding_model,
    Memory.source_turn_id,
    Memory.source_conversation_id,
    Memory.version,
    Memory.status,
    Memory.created_at,
    Memory.updated_at,
    Memory.supersedes_id,
    Memory.request_key,
)
_TOOL_READ_COLUMNS = (
    ToolCall.id,
    ToolCall.turn_id,
    ToolCall.request_id,
    ToolCall.name,
    ToolCall.state,
    ToolCall.result,
    ToolCall.error_code,
    ToolCall.arguments_hash,
)
DENIED_STATUSES = frozenset({"superseded", "inactive", "excluded"})
# Retraction lines: one bounded, non-privileged reminder per hidden memory that
# originated in this conversation, so restatements of a forgotten fact in the
# history window are not confirmed back to the user.
RETRACTION_PREFIX = "retraction:"
RETRACTION_LIMIT = 5
# The line never quotes the fact: the fact itself is what must not reach the model again.
RETRACTION_TEMPLATE = (
    "The user asked you to forget something they told you earlier. Do not use, repeat or confirm it, and do not "
    "say what it was."
)


@dataclass(frozen=True)
class MemoryCandidates:
    """Permission-split memory view for one revision."""

    included: tuple[ContextCandidate, ...] = ()
    denied: tuple[ContextCandidate, ...] = ()
    inactive: tuple[ContextCandidate, ...] = ()
    blocked_sources: frozenset[str] = frozenset()
    rows: Mapping[str, Memory] = field(default_factory=dict)
    # Derived from the same blocked rows: hidden memories from this conversation.
    retractions: tuple[ContextCandidate, ...] = ()

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
    retractions: tuple[ContextCandidate, ...] = ()

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
    blocked_rows = await blocked_memory_rows(db, owner_id, conversation_id)
    rows = (
        await db.scalars(
            select(Memory)
            .where(Memory.owner_id == owner_id, Memory.status == "active")
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(MEMORY_CANDIDATE_LIMIT)
        )
    ).all()
    return _memory_candidates_from_rows(blocked_rows, rows, conversation_id=conversation_id)


def _memory_candidates_from_rows(
    blocked_rows: Sequence[Memory], rows: Sequence[Memory], *, conversation_id: str
) -> MemoryCandidates:
    """Split already-loaded blocked and active rows into the revision's permission view."""
    excluded_ids = frozenset(row.id for row in blocked_rows)
    sources = frozenset(row.source_turn_id for row in blocked_rows if row.source_turn_id is not None)
    active_by_id = {row.id: row for row in rows}
    denied_by_id = {row.id: row for row in blocked_rows}
    included: list[ContextCandidate] = [
        ContextCandidate(
            id=row.id,
            role="memory",
            text=row.content,
            origin="memory",
            source=row.source_turn_id,
            status="active",
            created_at=row.created_at,
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
                created_at=row.created_at,
            )
        )
    return MemoryCandidates(
        included=tuple(included),
        denied=tuple(denied),
        inactive=(),
        blocked_sources=sources,
        rows=active_by_id,
        retractions=_retraction_candidates_from_rows(blocked_rows, conversation_id=conversation_id),
    )


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


def snapshot_of(turn: Turn, snapshots: Mapping[str, dict] | None = None) -> dict:
    """Read a turn snapshot without triggering a deferred-column load."""
    if snapshots is not None and turn.id in snapshots:
        return snapshots[turn.id] or {}
    if "snapshot" in inspect(turn).unloaded:
        return {}
    return turn.snapshot or {}


def _dependency_ids(turn: Turn, snapshots: Mapping[str, dict] | None = None) -> set[str]:
    """Record ids one turn can inherit dependencies from."""
    references: set[str] = set()
    if turn.request_id.startswith("reply:"):
        references.add(turn.request_id.removeprefix("reply:"))
    snapshot = snapshot_of(turn, snapshots)
    derivation = snapshot.get("derivation")
    values = derivation.get("record_ids") if isinstance(derivation, dict) else snapshot.get("included_record_ids")
    if isinstance(values, list):
        # Synthetic retraction ids are not stored records; they never propagate.
        references.update(
            value for value in values if isinstance(value, str) and not value.startswith(RETRACTION_PREFIX)
        )
    return references


def _retraction_candidates_from_rows(rows: Sequence[Memory], *, conversation_id: str) -> tuple[ContextCandidate, ...]:
    """Bounded retraction blocks from already-loaded memory rows.

    Applies the same predicate (hidden, from this conversation), order (newest
    ``updated_at`` then id) and limit as ``retraction_candidates``' SQL. The turn
    start passes the owner's blocked rows, which is correct only because
    ``blocked_memory_rows`` seeds every hidden memory of the owner: if that read is
    ever bounded or scoped, retraction blocks would silently change.
    ``test_retractions_from_blocked_rows_match_the_query`` pins that dependency.
    """
    hidden = [row for row in rows if row.status == "hidden" and row.source_conversation_id == conversation_id]
    newest = sorted(hidden, key=lambda row: (row.updated_at, row.id), reverse=True)[:RETRACTION_LIMIT]
    return tuple(
        ContextCandidate(
            id=RETRACTION_PREFIX + row.id,
            role="memory",
            text=RETRACTION_TEMPLATE,
            origin="memory",
            source="retraction",
            created_at=row.created_at,
        )
        for row in sorted(newest, key=lambda item: item.id)
        if row.content
    )


async def retraction_candidates(
    db: AsyncSession, *, owner_id: str, conversation_id: str
) -> tuple[ContextCandidate, ...]:
    """Bounded retraction blocks for hidden memories that came from this conversation.

    Derived from live status, so unhiding removes the block. Deleted memories have
    their text wiped and are never quoted. The candidates use the memory origin, so
    they reach the provider only through the inert untrusted-data envelope (#79).
    """
    rows = (
        await db.scalars(
            select(Memory)
            .where(
                Memory.owner_id == owner_id,
                Memory.status == "hidden",
                Memory.source_conversation_id == conversation_id,
            )
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(RETRACTION_LIMIT)
        )
    ).all()
    return _retraction_candidates_from_rows(rows, conversation_id=conversation_id)


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
    snapshots: Mapping[str, dict] | None = None,
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
            if snapshots is not None and "snapshot" not in inspect(row).unloaded:
                snapshots.setdefault(row.id, row.snapshot or {})
            frontier.update(_dependency_ids(row, snapshots))

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
    snapshots: Mapping[str, dict] | None = None,
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
        frontier.update(_dependency_ids(turn, snapshots))
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
                snapshots=snapshots,
            )
            if exceeded:
                return True, tools


async def _recent_turns(db: AsyncSession, conversation_id: str, *, limit: int) -> list[Turn]:
    """Deterministic recent window, returned in chronological model order."""
    rows = (
        await db.scalars(
            select(Turn)
            .options(
                load_only(
                    Turn.id,
                    Turn.conversation_id,
                    Turn.request_id,
                    Turn.role,
                    Turn.text,
                    Turn.status,
                    Turn.created_at,
                )
            )
            .where(Turn.conversation_id == conversation_id, Turn.status == "completed")
            .order_by(Turn.created_at.desc(), Turn.id.desc())
            .limit(limit)
        )
    ).all()
    return list(reversed(rows))


_TURN_LABELS = (
    "turn_id",
    "turn_conversation_id",
    "turn_request_id",
    "turn_role",
    "turn_text",
    "turn_status",
    "turn_created_at",
)
_MEMORY_LABELS = (
    "memory_id",
    "memory_owner_id",
    "memory_subject",
    "memory_content",
    "memory_embedding_model",
    "memory_source_turn_id",
    "memory_source_conversation_id",
    "memory_version",
    "memory_status",
    "memory_created_at",
    "memory_updated_at",
    "memory_supersedes_id",
    "memory_request_key",
)
_TOOL_LABELS = (
    "tool_id",
    "tool_turn_id",
    "tool_request_id",
    "tool_name",
    "tool_state",
    "tool_result",
    "tool_error_code",
    "tool_arguments_hash",
)


def _labeled_columns(columns, labels):
    return tuple(column.label(label) for column, label in zip(columns, labels, strict=True))


def _null_columns(columns, labels):
    """Fresh typed NULLs per union part; sharing cast expressions aliases them on SQLite."""
    return tuple(cast(null(), column.type).label(label) for column, label in zip(columns, labels, strict=True))


def _combined_read_statement(owner_id: str, conversation_id: str, *, history_limit: int):
    """One UNION ALL statement returning the blocked rows, active memories, tools and window.

    Every part projects the same labelled column list (turn columns, a
    discriminator, memory columns, tool columns), so the result hydrates the same
    ORM shapes the four individual reads produced.  Each part keeps its own
    ORDER BY/LIMIT; Python restores the chronological orders after the union.
    """
    blocked = select(
        *_null_columns(_TURN_READ_COLUMNS, _TURN_LABELS),
        literal("blocked").label("part"),
        *_labeled_columns(_MEMORY_READ_COLUMNS, _MEMORY_LABELS),
        *_null_columns(_TOOL_READ_COLUMNS, _TOOL_LABELS),
    ).where(
        Memory.owner_id == owner_id,
        Memory.id.in_(blocked_memory_ids_select(owner_id, conversation_id)),
    )
    active = (
        select(
            *_null_columns(_TURN_READ_COLUMNS, _TURN_LABELS),
            literal("active").label("part"),
            *_labeled_columns(_MEMORY_READ_COLUMNS, _MEMORY_LABELS),
            *_null_columns(_TOOL_READ_COLUMNS, _TOOL_LABELS),
        )
        .where(Memory.owner_id == owner_id, Memory.status == "active")
        .order_by(Memory.updated_at.desc(), Memory.id.desc())
        .limit(MEMORY_CANDIDATE_LIMIT)
    )
    tools = (
        select(
            *_labeled_columns(_TURN_READ_COLUMNS, _TURN_LABELS),
            literal("tool").label("part"),
            *_null_columns(_MEMORY_READ_COLUMNS, _MEMORY_LABELS),
            *_labeled_columns(_TOOL_READ_COLUMNS, _TOOL_LABELS),
        )
        .join(Turn, ToolCall.turn_id == Turn.id)
        .where(
            Turn.conversation_id == conversation_id,
            Turn.status == "completed",
            ToolCall.state == "completed",
        )
        .order_by(Turn.created_at.desc(), ToolCall.id)
        .limit(TOOL_RESULT_LIMIT)
    )
    window = (
        select(
            *_labeled_columns(_TURN_READ_COLUMNS, _TURN_LABELS),
            literal("window").label("part"),
            *_null_columns(_MEMORY_READ_COLUMNS, _MEMORY_LABELS),
            *_null_columns(_TOOL_READ_COLUMNS, _TOOL_LABELS),
        )
        .where(Turn.conversation_id == conversation_id, Turn.status == "completed")
        .order_by(Turn.created_at.desc(), Turn.id.desc())
        .limit(history_limit * HISTORY_WINDOW_MULTIPLIER)
    )

    def wrapped(part):
        # SQLite rejects parenthesised operands in a compound SELECT, so each
        # ordered/limited part becomes a FROM subquery (valid on both dialects).
        subquery = part.subquery()
        return select(*subquery.c)

    return union_all(blocked, wrapped(active), wrapped(tools), wrapped(window))


def _turn_from_combined_row(row) -> Turn:
    return Turn(
        id=row[0],
        conversation_id=row[1],
        request_id=row[2],
        role=row[3],
        text=row[4],
        status=row[5],
        created_at=row[6],
    )


def _memory_from_combined_row(row) -> Memory:
    return Memory(
        id=row[8],
        owner_id=row[9],
        subject=row[10],
        content=row[11],
        embedding_model=row[12],
        source_turn_id=row[13],
        source_conversation_id=row[14],
        version=row[15],
        status=row[16],
        created_at=row[17],
        updated_at=row[18],
        supersedes_id=row[19],
        request_key=row[20],
    )


def _tool_from_combined_row(row) -> ToolCall:
    return ToolCall(
        id=row[21],
        turn_id=row[22],
        request_id=row[23],
        name=row[24],
        state=row[25],
        result=row[26],
        error_code=row[27],
        arguments_hash=row[28],
    )


async def _combined_context_rows(
    db: AsyncSession, *, owner_id: str, conversation_id: str, history_limit: int
) -> tuple[list[Memory], list[Memory], list[tuple[ToolCall, Turn]], list[Turn]]:
    """Hydrate the four turn-start reads from their single combined statement.

    The hydrated rows are transient ORM objects built from a UNION ALL result,
    not tracked by the session identity map, so callers must not mutate or
    flush them.
    """
    rows = (await db.execute(_combined_read_statement(owner_id, conversation_id, history_limit=history_limit))).all()
    blocked_rows: list[Memory] = []
    active_rows: list[Memory] = []
    tool_rows: list[tuple[ToolCall, Turn]] = []
    window: list[Turn] = []
    for row in rows:
        part = row[7]
        if part == "blocked":
            blocked_rows.append(_memory_from_combined_row(row))
        elif part == "active":
            active_rows.append(_memory_from_combined_row(row))
        elif part == "tool":
            tool_rows.append((_tool_from_combined_row(row), _turn_from_combined_row(row)))
        else:
            window.append(_turn_from_combined_row(row))
    # The union preserves each part's rows, not their order.  Restore the
    # deterministic orders the separate reads used.
    active_rows.sort(key=lambda memory: (memory.updated_at, memory.id), reverse=True)
    tool_rows.sort(key=lambda pair: pair[0].id)
    tool_rows.sort(key=lambda pair: pair[1].created_at, reverse=True)
    window.sort(key=lambda turn: (turn.created_at, turn.id))
    return blocked_rows, active_rows, tool_rows, window


async def _combined_context_read(
    db: AsyncSession, *, owner_id: str, conversation_id: str, history_limit: int
) -> tuple[MemoryCandidates, list[tuple[ToolCall, Turn]], list[Turn]]:
    blocked_rows, active_rows, tool_rows, window = await _combined_context_rows(
        db, owner_id=owner_id, conversation_id=conversation_id, history_limit=history_limit
    )
    memories = _memory_candidates_from_rows(blocked_rows, active_rows, conversation_id=conversation_id)
    return memories, tool_rows, window


def _known_tool_dependencies(turn: Turn, snapshots: Mapping[str, dict] | None = None) -> bool:
    values = snapshot_of(turn, snapshots).get("included_record_ids")
    return isinstance(values, list) and all(isinstance(value, str) for value in values)


async def read_context_inputs(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    memory_reader: MemoryReader | None = None,
    history_limit: int = HISTORY_LIMIT,
    combined_read: bool = False,
) -> ContextInputs:
    """Read everything the compiler may consider for the next turn.

    ``combined_read=True`` is ignored when a ``memory_reader`` is injected.
    The combined single statement is used only when no reader is supplied.
    """
    if combined_read and memory_reader is None:
        memories, tool_rows, window = await _combined_context_read(
            db, owner_id=owner_id, conversation_id=conversation_id, history_limit=history_limit
        )
    else:
        reader = memory_reader or _OrmMemoryReader()
        memories = await reader.candidates(db, owner_id=owner_id, conversation_id=conversation_id)
        tool_rows = (
            await db.execute(
                select(ToolCall, Turn)
                .join(Turn, ToolCall.turn_id == Turn.id)
                .options(
                    load_only(
                        Turn.id,
                        Turn.conversation_id,
                        Turn.request_id,
                        Turn.role,
                        Turn.text,
                        Turn.status,
                        Turn.created_at,
                    )
                )
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
    policy_active = bool(memories.excluded_ids or memories.blocked_sources)
    by_id = {turn.id: turn for turn in window}
    closure_exceeded = False
    closure_tools: dict[str, tuple[ToolCall, Turn]] = {}
    snapshots: dict[str, dict] = {}
    if policy_active:
        # Tool rows are bounded by the tool-result limit, not the history window.
        # Their parents must join the evaluated set so an exclusion can propagate
        # through older derivations; no traversal happens with no active policy.
        for _, parent in tool_rows:
            by_id.setdefault(parent.id, parent)
        snapshots = dict((await db.execute(select(Turn.id, Turn.snapshot).where(Turn.id.in_(list(by_id))))).all())
        closure_exceeded, closure_tools = await _dependency_closure(
            db, owner_id=owner_id, conversation_id=conversation_id, loaded=by_id, snapshots=snapshots
        )
    blocked = set(memories.excluded_ids | memories.blocked_sources)
    # Hiding a memory must also hide the history that restates it, user and assistant turns alike, or the fact
    # reaches the model again through the window. Pure and bounded: both lists are already loaded.
    blocked |= restating_turn_ids(
        ((turn.id, turn.text or "") for turn in by_id.values()), (candidate.text for candidate in memories.denied)
    )

    def dependencies(turn):
        snapshot = snapshot_of(turn, snapshots)
        derivation = snapshot.get("derivation")
        if isinstance(derivation, dict):
            values = derivation.get("record_ids")
        else:
            parent = by_id.get(turn.request_id.removeprefix("reply:")) if turn.request_id.startswith("reply:") else None
            values = snapshot_of(parent, snapshots).get("included_record_ids") if parent else None
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return None
        return frozenset(values)

    # Only generated assistant content inherits model-input dependencies. Independent
    # user history is retained unless it is the explicitly tracked source of a memory, or it restates a hidden,
    # deleted or conversation-excluded fact (#194; see restating_turn_ids above). A corrected (superseded) fact is
    # not forgotten, so its history is kept.
    # Unknown provenance or an exhausted closure fails closed while exclusions exist.
    fail_closed = closure_exceeded and policy_active
    turns = list(by_id.values())
    blocked_tools: set[str] = set()
    tool_dependencies: dict[str, frozenset[str]] = {}
    window_tool_ids = {call.id for call, _ in tool_rows}
    all_tools = list(tool_rows) + [pair for tool_id, pair in closure_tools.items() if tool_id not in window_tool_ids]
    for call, turn in all_tools:
        values = snapshot_of(turn, snapshots).get("included_record_ids")
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
                or (policy_active and not _known_tool_dependencies(turn, snapshots))
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
            # Tool calls carry no timestamp of their own; their parent turn places
            # them chronologically.
            created_at=turn.created_at,
        )
        for call, turn in tool_rows
        if call.id not in blocked_tools
    )
    # The ORM reader derives retractions from the blocked rows it already loaded;
    # any alternate reader keeps the separate read so its results are unchanged.
    retractions = (
        memories.retractions
        if memory_reader is None
        else await retraction_candidates(db, owner_id=owner_id, conversation_id=conversation_id)
    )
    return ContextInputs(
        history=history,
        memories=memories,
        tool_results=tool_results,
        blocked_ids=frozenset(blocked | blocked_tools),
        retractions=retractions,
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
    "RETRACTION_PREFIX",
    "TOOL_RESULT_LIMIT",
    "location_evidence",
    "orm_memory_candidates",
    "read_context_inputs",
    "retraction_candidates",
]
