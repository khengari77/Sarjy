"""Forgetting a fact mid-conversation also removes the earlier turns that restate it (#194)."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.domain.restatement import content_tokens
from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder

FACT = "My favorite color is blue"


def configured(tmp_path):
    file = tmp_path / "forget.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )
    return file, settings


def add_turn(file, chat, turn_id, role, text, at, *, request=None, snapshot=None):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                turn_id,
                chat,
                request or turn_id,
                role,
                text,
                "completed",
                at,
                json.dumps(snapshot) if snapshot else None,
            ),
        )


def seed_history(file, chat, suffix=""):
    """Seed the fact's user turn, an assistant echo derived from it, and an unrelated turn."""
    user_id = f"fact-user{suffix}"
    assistant_id = f"fact-assistant{suffix}"
    weather_id = f"weather{suffix}"
    add_turn(file, chat, user_id, "user", "my favorite color is blue", "2026-11-12T00:00:01+00:00")
    add_turn(
        file,
        chat,
        assistant_id,
        "assistant",
        "Nice, blue it is",
        "2026-11-12T00:00:02+00:00",
        request=f"reply:{user_id}",
        snapshot={"derivation": {"turn_id": user_id, "record_ids": [user_id]}},
    )
    add_turn(file, chat, weather_id, "user", "what is the weather in Tripoli", "2026-11-12T00:00:03+00:00")
    return user_id, assistant_id, weather_id


def create_memory(client, chat, content, key, *, source_turn_id=None):
    payload = {"content": content, "idempotency_key": key}
    if source_turn_id is not None:
        payload["source_turn_id"] = source_turn_id
    response = client.post(f"/v1/conversations/{chat}/memories", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def start_turn(client, chat, owner, file, *, request_id, text="What else do I remember?"):
    """Start one turn; the conversation fixture supplies the owner but the session is created here."""
    session_id = f"session-{chat}"
    room_name = f"room-{chat}"
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT OR IGNORE INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                chat,
                owner,
                room_name,
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                json.dumps({}),
            ),
        )
    headers = {"x-agent-secret": "test-service-secret", "x-session-id": session_id, "x-room-name": room_name}
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": text})
    assert response.status_code == 200, response.text
    return response.json()["context"]


def exclude_in_conversation(client, chat, memory_id, key):
    proposal = client.post(f"/v1/conversations/{chat}/context/exclusions", json={"memory_ids": [memory_id]})
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    applied = client.post(
        f"/v1/conversations/{chat}/context/confirm",
        json={"proposal": body["proposal"], "expected_revision": body["revision"], "idempotency_key": key},
    )
    assert applied.status_code == 200, applied.text
    return applied.json()


def rendered(context):
    return json.dumps(context["messages"], ensure_ascii=False)


def retraction_messages(context):
    return [message for message in context["messages"] if "id=retraction" in message["content"]]


def test_hiding_a_memory_drops_restating_history_and_the_assistant_echo(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        blue_user, assistant_echo, weather = seed_history(file, chat)
        memory = create_memory(client, chat, FACT, "favorite")
        assert client.post(f"/v1/memories/{memory}/hide").status_code == 200

        context = start_turn(client, chat, owner, file, request_id="after-hide")

        messages = rendered(context).lower()
        assert "blue" not in messages
        assert "Nice, blue it is" not in rendered(context)
        assert "what is the weather in Tripoli" in rendered(context)
        assert blue_user not in context["included_record_ids"]
        assert assistant_echo not in context["included_record_ids"]
        assert weather in context["included_record_ids"]
        assert memory not in context["included_record_ids"]


def test_unhiding_a_memory_restores_the_restating_history(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        blue_user, assistant_echo, weather = seed_history(file, chat)
        memory = create_memory(client, chat, FACT, "favorite")
        assert client.post(f"/v1/memories/{memory}/hide").status_code == 200

        hidden = start_turn(client, chat, owner, file, request_id="hidden")
        assert "blue" not in rendered(hidden).lower()
        assert blue_user not in hidden["included_record_ids"]
        assert assistant_echo not in hidden["included_record_ids"]

        assert client.post(f"/v1/memories/{memory}/unhide").json()["status"] == "active"
        restored = start_turn(client, chat, owner, file, request_id="restored")

        assert "blue" in rendered(restored).lower()
        assert blue_user in restored["included_record_ids"]
        assert assistant_echo in restored["included_record_ids"]
        assert weather in restored["included_record_ids"]
        assert memory in restored["included_record_ids"]


def test_a_conversation_exclusion_blocks_restating_turns_only_in_that_conversation(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        other = client.post("/v1/conversations").json()["id"]
        blue_user, assistant_echo, weather = seed_history(file, chat)
        other_user, other_echo, other_weather = seed_history(file, other, suffix="-other")
        memory = create_memory(client, chat, FACT, "favorite")

        applied = exclude_in_conversation(client, chat, memory, "exclude-favorite")
        assert applied["excluded_ids"] == [memory]

        excluded = start_turn(client, chat, owner, file, request_id="excluded")
        assert blue_user not in excluded["included_record_ids"]
        assert assistant_echo not in excluded["included_record_ids"]
        assert weather in excluded["included_record_ids"]
        assert memory not in excluded["included_record_ids"]
        assert memory in excluded["excluded_record_ids"]
        assert "blue" not in rendered(excluded).lower()

        elsewhere = start_turn(client, other, owner, file, request_id="elsewhere")
        assert other_user in elsewhere["included_record_ids"]
        assert other_echo in elsewhere["included_record_ids"]
        assert other_weather in elsewhere["included_record_ids"]
        assert "my favorite color is blue" in rendered(elsewhere)


def test_permanent_delete_leaves_no_retraction_line_and_no_restating_text(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        blue_user, assistant_echo, weather = seed_history(file, chat)
        memory = create_memory(client, chat, FACT, "favorite", source_turn_id=blue_user)

        deleted = client.delete(f"/v1/memories/{memory}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"id": memory, "status": "deleted", "changed": True}

        context = start_turn(client, chat, owner, file, request_id="after-delete")

        assert retraction_messages(context) == []
        assert "retraction:" not in "".join(context["included_record_ids"])
        assert "blue" not in rendered(context).lower()
        assert "my favorite color is blue" not in rendered(context)
        assert "Nice, blue it is" not in rendered(context)
        assert "what is the weather in Tripoli" in rendered(context)
        assert blue_user not in context["included_record_ids"]
        assert assistant_echo not in context["included_record_ids"]
        assert weather in context["included_record_ids"]


def test_the_retraction_line_never_contains_the_forgotten_fact(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        seed_history(file, chat)
        memory = create_memory(client, chat, FACT, "favorite")
        assert client.post(f"/v1/memories/{memory}/hide").status_code == 200

        context = start_turn(client, chat, owner, file, request_id="after-hide")
        retractions = retraction_messages(context)
        assert len(retractions) == 1
        assert "do not say what it was" in retractions[0]["content"].lower()

        tokens = content_tokens(FACT)
        assert tokens == frozenset({"favorite", "color", "blue"})
        for token in tokens:
            assert token not in retractions[0]["content"].lower()
            assert token not in json.dumps(context["model_request"], ensure_ascii=False).lower()
            assert token not in rendered(context).lower()
