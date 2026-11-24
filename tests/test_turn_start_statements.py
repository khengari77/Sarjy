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

# Measured on the same fixture: the pre-#224 path issued 12 statements and the
# three collapses in this change leave 9.
STATEMENTS_BEFORE = 12
STATEMENTS_AFTER = 9
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


def test_turn_start_uses_three_fewer_statements(tmp_path):
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


def test_window_and_tool_reads_do_not_select_the_snapshot_column(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        with capture_statements() as statements:
            start_turn(client, headers)
        window = [statement for statement in statements if "SELECT turns.id, turns.conversation_id" in statement]
        tools = [statement for statement in statements if "FROM tool_calls JOIN turns" in statement]
        assert window and tools
        assert all("snapshot" not in statement for statement in window + tools)


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
