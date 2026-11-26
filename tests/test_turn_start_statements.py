"""Statement-count regressions for POST /internal/agent/turn/start (#224 section C)."""

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

# Measured on the same fixture: the pre-#224 path issued 12 statements, the
# three #224 collapses left 9, the blocked-memory collapse left 8, folding the
# session guard and retractions into reads that already run left 6, and the
# one-statement turn-start read leaves 3 (lock, combined read, insert).
STATEMENTS_BEFORE = 12
STATEMENTS_AFTER = 3
START = "/internal/agent/turn/start"


def configured(tmp_path):
    file = tmp_path / "turn-start.db"
    return file, Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )


def seed(file, chat, *, excluded=False):
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        for index, content in enumerate(("one", "two", "three")):
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,"
                "created_at,updated_at) VALUES (?,?,?, 'text-embedding-3-small', ?, 1, 'active', ?, ?)",
                (
                    f"mem-{index}",
                    owner,
                    content,
                    chat,
                    f"2026-01-01T00:00:0{index}+00:00",
                    f"2026-01-01T00:00:0{index}+00:00",
                ),
            )
        for index in range(5):
            db.execute(
                "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"old-{index}",
                    chat,
                    f"old-req-{index}",
                    "user",
                    f"History {index}",
                    "completed",
                    f"2026-01-01T00:00:0{index}+00:00",
                    json.dumps({"included_record_ids": ["system"]}),
                ),
            )
        if excluded:
            db.execute(
                "INSERT INTO context_operations (id,owner_id,conversation_id,command_type,payload,idempotency_key,"
                "outcome,created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("op-1", owner, chat, "exclude", "{}", "key-1", "applied", "2026-01-01T00:00:00+00:00"),
            )
            db.execute(
                "INSERT INTO context_exclusions (id,owner_id,conversation_id,memory_id,operation_id,scope,active,"
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("ex-1", owner, chat, "mem-1", "op-1", "conversation", 1, "2026-01-01T00:00:00+00:00"),
            )
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
            "VALUES (?,?,?,?,?,?,?)",
            ("session", chat, owner, "room", "active", (datetime.now(UTC) + timedelta(minutes=10)).isoformat(), "{}"),
        )
        db.commit()
    return {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}


@contextmanager
def capture_statements():
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", capture)


def start_turn(client, headers):
    response = client.post(START, headers=headers, json={"request_id": "next", "text": "Hello"})
    assert response.status_code == 200, response.text
    return response.json()["context"]


def test_turn_start_statement_budget(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        with capture_statements() as statements:
            start_turn(client, headers)
        after = len(statements)
        assert after == STATEMENTS_AFTER
        assert after <= STATEMENTS_BEFORE - 3
        # The session row rides the locked conversation read and the scope predicate
        # rides the combined read, so neither read is repeated.
        session_reads = [statement for statement in statements if "voice_sessions" in statement]
        assert len(session_reads) == 1
        assert "JOIN voice_sessions" in session_reads[0]
        assert "LEFT OUTER JOIN turns" in session_reads[0]
        scope_reads = [statement for statement in statements if "memories.source_conversation_id = ?" in statement]
        assert all("UNION ALL" in statement for statement in scope_reads)


def test_window_and_tool_reads_do_not_select_the_snapshot_column(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        with capture_statements() as statements:
            start_turn(client, headers)
        combined = [statement for statement in statements if "UNION ALL" in statement]
        assert len(combined) == 1
        statement = combined[0]
        # The tool join and the recent-turns window are parts of this one read.
        assert "FROM tool_calls JOIN turns" in statement
        assert "FROM turns" in statement
        assert "snapshot" not in statement
        assert "memories.embedding AS" not in statement


def test_combined_read_matches_the_four_individual_reads(tmp_path):
    import asyncio

    from app.infrastructure.database import ToolCall, Turn
    from app.services.context_assembly import (
        HISTORY_LIMIT,
        HISTORY_WINDOW_MULTIPLIER,
        TOOL_RESULT_LIMIT,
        _combined_context_rows,
        _memory_candidates_from_rows,
        _recent_turns,
        orm_memory_candidates,
    )
    from app.services.memory_eligibility import blocked_memory_rows
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import load_only

    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        seed(file, chat)
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        db.execute(
            "INSERT INTO memories (id,owner_id,content,embedding_model,source_turn_id,source_conversation_id,"
            "version,status,created_at,updated_at) VALUES (?,?,?, 'text-embedding-3-small', ?, ?, 1, 'hidden', ?, ?)",
            (
                "mem-hidden",
                owner,
                "a hidden fact",
                "old-4",
                chat,
                "2026-01-01T00:00:09+00:00",
                "2026-01-01T00:00:09+00:00",
            ),
        )
        for tool_id, turn_id in (("tool-a", "old-2"), ("tool-b", "old-4")):
            db.execute(
                "INSERT INTO tool_calls (id,turn_id,request_id,name,state,result) VALUES (?,?,?,?,?,?)",
                (tool_id, turn_id, f"req-{tool_id}", "get_current_weather", "completed", json.dumps({"ok": tool_id})),
            )
        db.commit()

    async def compare():
        engine = create_async_engine(f"sqlite+aiosqlite:///{file}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                reference_blocked = await blocked_memory_rows(db, owner, chat)
                reference_memories = await orm_memory_candidates(db, owner_id=owner, conversation_id=chat)
                reference_tools = (
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
                            Turn.conversation_id == chat,
                            Turn.status == "completed",
                            ToolCall.state == "completed",
                        )
                        .order_by(Turn.created_at.desc(), ToolCall.id)
                        .limit(TOOL_RESULT_LIMIT)
                    )
                ).all()
                reference_window = await _recent_turns(db, chat, limit=HISTORY_LIMIT * HISTORY_WINDOW_MULTIPLIER)
                combined = await _combined_context_rows(
                    db, owner_id=owner, conversation_id=chat, history_limit=HISTORY_LIMIT
                )
        finally:
            await engine.dispose()
        return reference_blocked, reference_memories, reference_tools, reference_window, combined

    reference_blocked, reference_memories, reference_tools, reference_window, combined = asyncio.run(compare())
    combined_blocked, combined_active, combined_tools, combined_window = combined
    combined_memories = _memory_candidates_from_rows(combined_blocked, combined_active, conversation_id=chat)

    assert sorted(row.id for row in combined_blocked) == sorted(row.id for row in reference_blocked)
    assert [row.id for row in combined_active] == list(reference_memories.rows)
    assert combined_memories.included == reference_memories.included
    assert combined_memories.denied == reference_memories.denied
    assert combined_memories.blocked_sources == reference_memories.blocked_sources
    assert combined_memories.retractions == reference_memories.retractions
    assert [call.id for call, _ in combined_tools] == [call.id for call, _ in reference_tools]
    assert [call.result for call, _ in combined_tools] == [call.result for call, _ in reference_tools]
    assert [turn.id for turn in combined_window] == [turn.id for turn in reference_window]


def test_excluded_memory_is_absent_from_request_memories_and_messages(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat, excluded=True)
        context = start_turn(client, headers)
        assert "mem-1" not in context["included_record_ids"]
        assert {"mem-0", "mem-2"} <= set(context["included_record_ids"])
        memory_ids = {item["id"] for item in context["model_request"]["memories"]}
        rendered = json.dumps(context["model_request"]["messages"], ensure_ascii=False)
        message_ids = {memory_id for memory_id in ("mem-0", "mem-1", "mem-2") if f"id={memory_id}" in rendered}
        assert memory_ids == message_ids == {"mem-0", "mem-2"}


def test_with_an_exclusion_the_snapshots_are_loaded_once_and_provenance_still_applies(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat, excluded=True)
        with capture_statements() as statements:
            context = start_turn(client, headers)
        snapshot_reads = [s for s in statements if s.startswith("SELECT turns.id, turns.snapshot")]
        assert len(snapshot_reads) == 1
        assert "mem-1" not in context["included_record_ids"]


def test_a_reader_that_supplies_no_rows_still_gets_its_memory_records(tmp_path, monkeypatch):
    import dataclasses

    from app.services import context_assembly

    original = context_assembly.orm_memory_candidates

    async def without_rows(*args, **kwargs):
        return dataclasses.replace(await original(*args, **kwargs), rows={})

    monkeypatch.setattr(context_assembly, "orm_memory_candidates", without_rows)
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        context = start_turn(client, headers)
        memory_ids = {item["id"] for item in context["model_request"]["memories"]}
        assert memory_ids == {"mem-0", "mem-1", "mem-2"}


def test_a_missing_conversation_is_a_404_not_a_crash(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM conversations WHERE id=?", (chat,))
        response = client.post(START, headers=headers, json={"request_id": "r-gone", "text": "hello"})
        assert response.status_code == 404


def test_statement_log_is_opt_in(monkeypatch, caplog):
    import logging

    from app.infrastructure.timing import StatementTiming, current_statement_timing, install_statement_timing
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    install_statement_timing(engine)
    token = current_statement_timing.set(StatementTiming())
    try:
        with caplog.at_level(logging.INFO, logger="sarjy.statements"):
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            assert "statement 1" not in caplog.text
            monkeypatch.setenv("SARJY_LOG_STATEMENTS", "1")
            with engine.connect() as connection:
                connection.execute(text("SELECT 2"))
            assert "SELECT 2" in caplog.text
    finally:
        current_statement_timing.reset(token)
        engine.dispose()
