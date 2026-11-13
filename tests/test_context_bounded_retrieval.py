"""Bounded retrieval regressions for issue #81.

The assertions count materialized rows instead of timing the database, so they
remain meaningful on slow or fast machines.  PostgreSQL plans are inspected
separately and never claimed as a constant-runtime guarantee.
"""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.domain.context import ContextCandidate, compile_turn_context, validate_assembly
from app.infrastructure.database import (
    Base,
    ContextExclusion,
    ContextOperation,
    Conversation,
    Identity,
    Memory,
    ToolCall,
    Turn,
)
from app.services.context_assembly import (
    DEPENDENCY_CLOSURE_LIMIT,
    HISTORY_LIMIT,
    HISTORY_WINDOW_MULTIPLIER,
    MEMORY_CANDIDATE_LIMIT,
    TOOL_RESULT_LIMIT,
    read_context_inputs,
)
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def stamp(offset: int) -> str:
    return (BASE_TIME + timedelta(seconds=offset)).isoformat()


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class CountingSession:
    """Materialize results and record how many rows every statement returned."""

    def __init__(self, session):
        self._session = session
        self.records: list[tuple[str, int]] = []

    async def scalars(self, statement):
        rows = (await self._session.scalars(statement)).all()
        self.records.append((str(statement), len(rows)))
        return Rows(rows)

    async def execute(self, statement):
        rows = (await self._session.execute(statement)).all()
        self.records.append((str(statement), len(rows)))
        return Rows(rows)

    def counts(self, table: str) -> list[int]:
        return [count for statement, count in self.records if f"FROM {table}" in statement]


async def database(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Identity(id="alice", token_hash="a", expires_at="2030"))
        db.add(Identity(id="bob", token_hash="b", expires_at="2030"))
        await db.commit()
        db.add(Conversation(id="chat", owner_id="alice"))
        db.add(Conversation(id="other", owner_id="bob"))
        await db.commit()
    return engine, sessions


async def seed_turns(db, conversation_id: str, count: int, *, prefix: str = "turn") -> list[str]:
    rows = [
        {
            "id": f"{prefix}-{index:05d}",
            "conversation_id": conversation_id,
            "request_id": f"{prefix}-request-{index:05d}",
            "role": "user",
            "text": f"History {index}",
            "status": "completed",
            "created_at": stamp(index),
            "snapshot": None,
        }
        for index in range(count)
    ]
    await db.execute(insert(Turn), rows)
    return [row["id"] for row in rows]


async def seed_memories(
    db, owner_id: str, conversation_id: str, count: int, *, status: str = "active", prefix: str = "mem"
) -> list[str]:
    rows = [
        {
            "id": f"{prefix}-{status}-{index:05d}",
            "owner_id": owner_id,
            "content": f"Memory {index}",
            "embedding_model": "text-embedding-3-small",
            "source_conversation_id": conversation_id,
            "version": 1,
            "status": status,
            "created_at": stamp(index),
            "updated_at": stamp(index),
        }
        for index in range(count)
    ]
    await db.execute(insert(Memory), rows)
    return [row["id"] for row in rows]


async def add_exclusion(db, memory_id: str, *, conversation_id: str, owner_id: str) -> None:
    operation_id = f"op-{memory_id}"
    db.add(
        ContextOperation(
            id=operation_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
            command_type="exclude",
            payload={},
            idempotency_key=f"key-{memory_id}",
            outcome="applied",
            created_at=stamp(0),
        )
    )
    await db.flush()
    db.add(
        ContextExclusion(
            id=f"ex-{memory_id}",
            owner_id=owner_id,
            conversation_id=conversation_id,
            memory_id=memory_id,
            operation_id=operation_id,
            scope="conversation",
            active=True,
            created_at=stamp(0),
        )
    )


def update_turn_snapshot(turn_id: str, snapshot: dict):
    return Turn.__table__.update().where(Turn.id == turn_id).values(snapshot=snapshot)


def test_large_history_and_memory_sets_materialize_bounded_candidates(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "bounded.db")
        try:
            async with sessions() as db:
                await seed_memories(db, "alice", "chat", 3000)
                await seed_memories(db, "alice", "chat", 2000, status="inactive")
                await seed_memories(db, "bob", "other", 50, prefix="bob")
                await seed_turns(db, "chat", 3000)
                await seed_turns(db, "other", 500, prefix="other")
                await db.commit()
            readings = []
            for _ in range(2):
                async with sessions() as db:
                    counted = CountingSession(db)
                    inputs = await read_context_inputs(counted, owner_id="alice", conversation_id="chat")
                readings.append(inputs)
            inputs = readings[0]
            assert len(inputs.memories.included) == MEMORY_CANDIDATE_LIMIT
            assert len(inputs.memories.denied) == 0
            assert len(inputs.memories.inactive) == 0
            assert {candidate.id for candidate in inputs.memories.included} == {
                candidate.id for candidate in readings[1].memories.included
            }, "the candidate bound must be deterministic across equal timestamps"
            assert all(candidate.id.startswith("mem-active-") for candidate in inputs.memories.included)
            assert len(inputs.history) == HISTORY_LIMIT
            assert len(inputs.tool_results) <= TOOL_RESULT_LIMIT
            assert [candidate.id for candidate in inputs.history] == [
                f"turn-{index:05d}" for index in range(2980, 3000)
            ]
            assert inputs.history[-1].id == "turn-02999", "the newest/current turn stays included"
            assert max(counted.counts("memories")) <= MEMORY_CANDIDATE_LIMIT
            assert max(counted.counts("turns")) <= (
                HISTORY_LIMIT * HISTORY_WINDOW_MULTIPLIER + DEPENDENCY_CLOSURE_LIMIT + TOOL_RESULT_LIMIT
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_old_excluded_source_with_recent_derived_reply_and_tool_stays_blocked(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "derived.db")
        try:
            async with sessions() as db:
                await seed_turns(db, "chat", 600)
                await db.execute(
                    insert(Turn),
                    [
                        {
                            "id": "old-source",
                            "conversation_id": "chat",
                            "request_id": "old-source-request",
                            "role": "user",
                            "text": "I live in Tripoli",
                            "status": "completed",
                            "created_at": stamp(0),
                            "snapshot": None,
                        },
                        {
                            "id": "old-deriver",
                            "conversation_id": "chat",
                            "request_id": "old-deriver-request",
                            "role": "user",
                            "text": "What is the weather?",
                            "status": "completed",
                            "created_at": stamp(1),
                            "snapshot": {"included_record_ids": ["m-old", "system"]},
                        },
                        {
                            "id": "recent-derived-reply",
                            "conversation_id": "chat",
                            "request_id": "reply:old-deriver",
                            "role": "assistant",
                            "text": "Tripoli is warm",
                            "status": "completed",
                            "created_at": stamp(700),
                            "snapshot": None,
                        },
                    ],
                )
                await db.execute(update_turn_snapshot("turn-00599", {"included_record_ids": ["m-old", "system"]}))
                await db.execute(
                    insert(Memory),
                    [
                        {
                            "id": "m-old",
                            "owner_id": "alice",
                            "content": "I live in Tripoli",
                            "embedding_model": "text-embedding-3-small",
                            "source_turn_id": "old-source",
                            "source_conversation_id": "chat",
                            "version": 1,
                            "status": "active",
                            "created_at": stamp(0),
                            "updated_at": stamp(0),
                        }
                    ],
                )
                await add_exclusion(db, "m-old", conversation_id="chat", owner_id="alice")
                db.add(
                    ToolCall(
                        id="tool-derived",
                        turn_id="turn-00599",
                        request_id="tool-derived-request",
                        name="get_current_weather",
                        state="completed",
                        result={"city": "Tripoli"},
                    )
                )
                await db.commit()
            async with sessions() as db:
                counted = CountingSession(db)
                inputs = await read_context_inputs(counted, owner_id="alice", conversation_id="chat")
            ids = {candidate.id for candidate in inputs.history}
            assert "recent-derived-reply" not in ids
            assert "turn-00599" in ids, "independent user history remains eligible"
            assert "tool-derived" not in {candidate.id for candidate in inputs.tool_results}
            assert inputs.memories.excluded_ids == frozenset({"m-old"})
            assert max(counted.counts("turns")) <= HISTORY_LIMIT * HISTORY_WINDOW_MULTIPLIER + 10
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_unknown_provenance_fails_closed_while_exclusions_exist(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "unknown.db")
        try:
            async with sessions() as db:
                await seed_turns(db, "chat", 50)
                await seed_memories(db, "alice", "chat", 1)
                await db.execute(
                    insert(Turn),
                    [
                        {
                            "id": "assistant-unknown",
                            "conversation_id": "chat",
                            "request_id": "reply:missing-turn",
                            "role": "assistant",
                            "text": "Unverifiable suggestion",
                            "status": "completed",
                            "created_at": stamp(100),
                            "snapshot": None,
                        }
                    ],
                )
                await add_exclusion(db, "mem-active-00000", conversation_id="chat", owner_id="alice")
                db.add(
                    ToolCall(
                        id="tool-unknown",
                        turn_id="turn-00049",
                        request_id="tool-unknown-request",
                        name="get_current_weather",
                        state="completed",
                        result={"city": "Tripoli"},
                    )
                )
                await db.commit()
            async with sessions() as db:
                inputs = await read_context_inputs(db, owner_id="alice", conversation_id="chat")
            ids = {candidate.id for candidate in inputs.history}
            assert "assistant-unknown" not in ids
            assert "tool-unknown" not in {candidate.id for candidate in inputs.tool_results}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_exhausted_dependency_closure_fails_closed(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "closure.db")
        try:
            async with sessions() as db:
                referenced = await seed_turns(db, "chat", DEPENDENCY_CLOSURE_LIMIT + 200)
                await seed_memories(db, "alice", "chat", 1)
                await add_exclusion(db, "mem-active-00000", conversation_id="chat", owner_id="alice")
                await db.execute(
                    insert(Turn),
                    [
                        {
                            "id": "assistant-closure",
                            "conversation_id": "chat",
                            "request_id": "assistant-closure-request",
                            "role": "assistant",
                            "text": "Derived from too much history",
                            "status": "completed",
                            "created_at": stamp(10_000),
                            "snapshot": {"derivation": {"turn_id": "turn-00000", "record_ids": referenced}},
                        }
                    ],
                )
                await db.commit()
            async with sessions() as db:
                inputs = await read_context_inputs(db, owner_id="alice", conversation_id="chat")
            assert "assistant-closure" not in {candidate.id for candidate in inputs.history}
            assert inputs.tool_results == ()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_bounded_inputs_preserve_compiler_revision_and_hash_invariants(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "compile.db")
        try:
            async with sessions() as db:
                await seed_turns(db, "chat", 100)
                await seed_memories(db, "alice", "chat", 10)
                await db.commit()
            async with sessions() as db:
                inputs = await read_context_inputs(db, owner_id="alice", conversation_id="chat")
            compiled = compile_turn_context(
                revision=4,
                instructions="Rules",
                history=inputs.history,
                memories=inputs.memories.included,
                tool_results=inputs.tool_results,
                current_turn=ContextCandidate(id="current", role="user", text="Now", origin="history"),
                excluded_ids=inputs.excluded_ids,
            )
            snapshot = compiled.to_snapshot()
            validate_assembly(4, compiled.manifest, snapshot)
            assert compiled.manifest["context_revision"] == 4
            assert compiled.included_ids[-1] == "current"
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set (disposable PostgreSQL only)")
def test_postgres_retrieval_plans_use_additive_indexes_with_bounded_rows():
    asyncio.run(_postgres_plan_and_bounds())


async def _postgres_plan_and_bounds():
    env = os.environ | {"DATABASE_URL": POSTGRES_URL}
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    engine = create_async_engine(POSTGRES_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, chat = f"pg-owner-{uuid4()}", f"pg-chat-{uuid4()}"
    try:
        async with sessions() as db:
            db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
            await db.commit()
            db.add(Conversation(id=chat, owner_id=owner))
            await db.commit()
            await seed_memories(db, owner, chat, 3000)
            await seed_turns(db, chat, 3000)
            await db.commit()
            await db.execute(text("ANALYZE turns"))
            await db.execute(text("ANALYZE memories"))
            await db.execute(text("SET LOCAL enable_seqscan = off"))
            turn_plan = (
                await db.execute(
                    text(
                        "EXPLAIN (FORMAT JSON) SELECT id FROM turns "
                        "WHERE conversation_id = :chat AND status = 'completed' "
                        "ORDER BY created_at DESC, id DESC LIMIT :limit"
                    ),
                    {"chat": chat, "limit": HISTORY_LIMIT * HISTORY_WINDOW_MULTIPLIER},
                )
            ).scalar_one()
            memory_plan = (
                await db.execute(
                    text(
                        "EXPLAIN (FORMAT JSON) SELECT id FROM memories "
                        "WHERE owner_id = :owner AND status = 'active' "
                        "ORDER BY updated_at DESC, id DESC LIMIT :limit"
                    ),
                    {"owner": owner, "limit": MEMORY_CANDIDATE_LIMIT},
                )
            ).scalar_one()
            indexes = set(
                (
                    await db.execute(
                        text("SELECT indexname FROM pg_indexes WHERE tablename IN ('turns','memories','tool_calls')")
                    )
                ).scalars()
            )
            await db.rollback()
        assert "ix_turns_conversation_status_created" in indexes
        assert "ix_memories_owner_status_updated" in indexes
        assert "ix_tool_calls_turn_state" in indexes
        for plan in (turn_plan, memory_plan):
            rendered = str(plan)
            assert "Index" in rendered or "index" in rendered
            assert "Limit" in rendered
        async with sessions() as db:
            counted = CountingSession(db)
            inputs = await read_context_inputs(counted, owner_id=owner, conversation_id=chat)
        assert len(inputs.memories.included) <= MEMORY_CANDIDATE_LIMIT
        assert len(inputs.history) <= HISTORY_LIMIT
        assert max(counted.counts("memories")) <= MEMORY_CANDIDATE_LIMIT
        assert max(counted.counts("turns")) <= (
            HISTORY_LIMIT * HISTORY_WINDOW_MULTIPLIER + DEPENDENCY_CLOSURE_LIMIT + TOOL_RESULT_LIMIT
        )
    finally:
        await engine.dispose()
