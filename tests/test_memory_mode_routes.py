"""With memory off, nothing is proposed: no regex proposal at turn start and no model tool proposal."""

import sqlite3
from contextlib import closing

from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder
from test_memory_capture import add_turn_session, configured, memory_rows, turn

PROPOSE = "/internal/agent/tool/memory-propose"


def set_mode(file, owner, mode):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT OR REPLACE INTO user_settings (owner_id,speaking_speed,captions_visible,response_preference,"
            "memory_mode,revision,updated_at) VALUES (?,?,?,?,?,?,?)",
            (owner, 1.0, 1, "automatic", mode, 1, "2026-11-25T00:00:00+00:00"),
        )


def started(tmp_path, mode):
    file, settings = configured(tmp_path)
    client_cm = TestClient(create_app(settings, embedding_adapter=FakeEmbedder()))
    client = client_cm.__enter__()
    owner = client.post("/v1/demo-session").json()["id"]
    chat = client.post("/v1/conversations").json()["id"]
    headers = add_turn_session(file, chat, owner)
    if mode is not None:
        set_mode(file, owner, mode)
    return client_cm, client, file, headers


def test_regex_proposal_is_made_in_auto_and_ask_but_not_when_off(tmp_path):
    # (mode, a proposal is made, a spoken question is pending). In auto an ordinary fact is saved without asking.
    for mode, expected, asks in (
        ("auto", True, False),
        ("ask", True, True),
        ("off", False, False),
        (None, True, False),
    ):
        sub = tmp_path / f"{mode}"
        sub.mkdir()
        cm, client, file, headers = started(sub, mode)
        try:
            body = turn(client, headers, "My name is Sam", "fact-1").json()
            assert (body["memory_proposal"] is not None) is expected, mode
            pending = "pending" in body["context"]["model_request"]["instructions"].lower()
            assert pending is asks, mode
            assert memory_rows(file) == []
        finally:
            cm.__exit__(None, None, None)


def test_model_tool_proposal_is_rejected_when_off_and_nothing_is_recorded(tmp_path):
    cm, client, file, headers = started(tmp_path, "off")
    try:
        started_turn = turn(client, headers, "hello there", "t1").json()
        body = client.post(
            PROPOSE,
            headers=headers,
            json={"turn_id": started_turn["turn_id"], "request_id": "p1", "fact": "I live in Benghazi"},
        ).json()
        assert body["status"] == "rejected" and body["error"] == "MEMORY_OFF"
        assert "proposal" not in body and "off" in body["message"].lower()
        assert memory_rows(file) == []
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT COUNT(*) FROM memory_write_requests").fetchone()[0] == 0
    finally:
        cm.__exit__(None, None, None)


def test_a_proposal_armed_before_memory_was_turned_off_cannot_be_saved_afterwards(tmp_path):
    cm, client, file, headers = started(tmp_path, "auto")
    try:
        body = turn(client, headers, "My name is Sam", "fact-1").json()
        token = body["memory_proposal"]["token"]
        with closing(sqlite3.connect(file)) as db, db:
            owner = db.execute("SELECT owner_id FROM voice_sessions LIMIT 1").fetchone()[0]
        set_mode(file, owner, "off")

        saved = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": token, "decision": "save"}
        )
        assert saved.status_code == 409 and saved.json()["detail"]["code"] == "MEMORY_OFF"
        assert memory_rows(file) == []

        # Declining is always allowed, so the worker can still retire the question.
        discarded = client.post(
            "/internal/agent/memory/confirm", headers=headers, json={"token": token, "decision": "discard"}
        )
        assert discarded.status_code == 200 and memory_rows(file) == []
    finally:
        cm.__exit__(None, None, None)


def test_a_memory_mode_phrase_is_not_also_proposed_as_a_fact(tmp_path):
    cm, client, file, headers = started(tmp_path, None)
    try:
        for index, text in enumerate(("Remember things automatically", "remember automatically")):
            body = turn(client, headers, text, f"mode-{index}").json()
            assert body["memory_proposal"] is None, text
        body = turn(client, headers, "Remember that my name is Sam", "fact-1").json()
        assert body["memory_proposal"] is not None
    finally:
        cm.__exit__(None, None, None)
