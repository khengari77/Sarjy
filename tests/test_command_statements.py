"""Statement-count regressions for POST /internal/agent/command."""

import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

# Measured on this fixture: the command request used to issue four statements
# (guard, turn, conversation, then an unconditional UPDATE). The joined read
# and conditional supersede leave two.
STATEMENTS_BEFORE = 4
STATEMENTS_AFTER = 2
START = "/internal/agent/turn/start"
COMMAND = "/internal/agent/command"


def configured(tmp_path):
    file = tmp_path / "command.db"
    return file, Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )


def seed(file, chat, *, pending=False):
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        if pending:
            db.execute(
                "INSERT INTO context_operations (id,owner_id,conversation_id,command_type,payload,idempotency_key,"
                "outcome,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    "op-pending",
                    owner,
                    chat,
                    "exclude_proposal",
                    "{}",
                    "key-pending",
                    "proposed",
                    "2026-01-01T00:00:00+00:00",
                ),
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
    response = client.post(START, headers=headers, json={"request_id": "source", "text": "Tell me a story."})
    assert response.status_code == 200, response.text
    return response.json()["turn_id"]


def test_command_with_nothing_pending_issues_no_update(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        turn_id = start_turn(client, headers)
        with capture_statements() as statements:
            response = client.post(COMMAND, headers=headers, json={"turn_id": turn_id})
        assert response.json() == {"command": None, "memory_op": None}
        after = len(statements)
        assert after == STATEMENTS_AFTER
        assert after <= STATEMENTS_BEFORE - 2
        assert not [statement for statement in statements if statement.lstrip().upper().startswith("UPDATE")]


def test_command_with_a_pending_proposal_supersedes_it(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat, pending=True)
        turn_id = start_turn(client, headers)
        with capture_statements() as statements:
            response = client.post(COMMAND, headers=headers, json={"turn_id": turn_id})
        assert response.json() == {"command": None, "memory_op": None}
        assert [statement for statement in statements if statement.lstrip().upper().startswith("UPDATE")]
    with closing(sqlite3.connect(file)) as db, db:
        outcome = db.execute("SELECT outcome FROM context_operations WHERE id='op-pending'").fetchone()[0]
    assert outcome == "superseded"
