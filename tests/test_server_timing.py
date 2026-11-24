import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

TIMING_HEADERS = ("x-server-ms", "x-db-ms", "x-db-statements")


@pytest.fixture
def worker(tmp_path):
    database = tmp_path / "server-timing.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database}", agent_service_secret="test-service-secret", _env_file=None
    )
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        conversation = client.post("/v1/conversations").json()["id"]
        _insert_session(database, "session", conversation, owner, "room")
        headers = {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}
        yield client, headers


def _insert_session(database, session_id, conversation_id, owner_id, room_name):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                conversation_id,
                owner_id,
                room_name,
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                json.dumps({}),
            ),
        )


def test_internal_route_carries_timing_headers(worker):
    client, headers = worker
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": "turn-1", "text": "Hi"})
    assert response.status_code == 200
    for name in TIMING_HEADERS:
        assert response.headers[name].isdigit()
    assert int(response.headers["x-server-ms"]) >= 0
    assert int(response.headers["x-db-ms"]) >= 0
    assert int(response.headers["x-db-statements"]) > 0


def test_public_route_does_not_carry_timing_headers(worker):
    client, _ = worker
    response = client.post("/v1/demo-session")
    assert response.status_code == 200
    for name in TIMING_HEADERS:
        assert name not in response.headers


def test_slow_statements_are_always_logged(monkeypatch, caplog):
    """A statement over the threshold is a network or pooler stall and must reach the logs without a flag."""
    import logging

    from app.infrastructure import timing
    from sqlalchemy import create_engine, text

    monkeypatch.setattr(timing, "SLOW_STATEMENT_MS", 0.0)
    monkeypatch.setattr(timing.logger, "propagate", True)  # the production handler would bypass caplog
    engine = create_engine("sqlite://")
    timing.install_statement_timing(engine)
    token = timing.current_statement_timing.set(timing.StatementTiming())
    try:
        with caplog.at_level(logging.WARNING, logger="sarjy.statements"), engine.connect() as connection:
            connection.execute(text("SELECT 1   AS   one"))
    finally:
        timing.current_statement_timing.reset(token)
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("SELECT 1 AS one" in message for message in warnings)  # whitespace is collapsed
