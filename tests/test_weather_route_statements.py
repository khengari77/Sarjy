"""Statement-count and rejection regressions for POST /internal/agent/tool/weather."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import app.routes.worker as worker_module
from app.main import create_app
from fastapi.testclient import TestClient
from test_command_statements import capture_statements, configured, seed

WEATHER = "/internal/agent/tool/weather"


def _weather_turn(client, headers):
    response = client.post(
        "/internal/agent/turn/start", headers=headers, json={"request_id": "weather", "text": "Weather in Tripoli?"}
    )
    assert response.status_code == 200, response.text
    return response.json()["turn_id"]


def _weather(client, headers, turn_id, request_id="call-1", city="Tripoli"):
    return client.post(
        WEATHER,
        headers=headers,
        json={"turn_id": turn_id, "request_id": request_id, "city": city},
    )


def test_weather_session_guard_and_turn_are_one_joined_read(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)

    async def weather(city, country=None):
        return {"city": city}

    monkeypatch.setattr(worker_module, "get_current_weather", weather)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        turn_id = _weather_turn(client, headers)
        with capture_statements() as statements:
            response = _weather(client, headers, turn_id)
        assert response.status_code == 200
        assert response.json() == {"city": "Tripoli"}
    guard_reads = [
        statement
        for statement in statements
        if ("voice_sessions" in statement or "turns" in statement) and "tool_calls" not in statement
    ]
    assert len(guard_reads) == 1
    assert "voice_sessions" in guard_reads[0] and "turns" in guard_reads[0]


def test_every_weather_guard_rejection_still_happens(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)

    async def weather(city, country=None):
        return {"city": city}

    monkeypatch.setattr(worker_module, "get_current_weather", weather)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        headers = seed(file, chat)
        turn_id = _weather_turn(client, headers)

        def call(changed_headers=None, turn=turn_id):
            return _weather(client, {**headers, **(changed_headers or {})}, turn)

        assert call({"x-agent-secret": "wrong"}).status_code == 401
        assert call({"x-room-name": "other-room"}).status_code == 403
        assert call({"x-session-id": "missing"}).status_code == 403
        assert call(turn="no-such-turn").status_code == 404
        with closing(sqlite3.connect(file)) as db, db:
            db.execute("UPDATE voice_sessions SET status='ended' WHERE id='session'")
        assert call().status_code == 403

        with closing(sqlite3.connect(file)) as db, db:
            expires = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
            db.execute("UPDATE voice_sessions SET status='active', expires_at=? WHERE id='session'", (expires,))
        assert call().status_code == 403
