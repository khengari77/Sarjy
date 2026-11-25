"""Retraction line for hidden memories (step 1A) and the read-only blocks endpoint (step 1B)."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.main import create_app as real_create_app
from fastapi.testclient import TestClient


class FakeEmbedder:
    async def embed(self, db, text):
        return (1.0,) + (0.0,) * 1535


def create_app(settings):
    return real_create_app(settings, embedding_adapter=FakeEmbedder())


def configured(tmp_path):
    file = tmp_path / "blocks.db"
    return file, Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )


def sql(file, statement, params=()):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(statement, params)
        db.commit()


def count_rows(file, tables):
    with closing(sqlite3.connect(file)) as db:
        return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}


def add_memory(file, chat, memory_id, content, status="active", source_turn=None):
    sql(
        file,
        "INSERT INTO memories (id,owner_id,content,embedding_model,source_turn_id,source_conversation_id,version,status,"
        "created_at,updated_at) VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), ?, 'text-embedding-3-small', "
        "?, ?, 1, ?, '2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
        (memory_id, chat, content, source_turn, chat, status),
    )


def add_turn(file, chat, turn_id, role, text, at, snapshot=None, request=None):
    sql(
        file,
        "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) VALUES (?,?,?,?,?,?,?,?)",
        (turn_id, chat, request or turn_id, role, text, "completed", at, json.dumps(snapshot) if snapshot else None),
    )


def add_session(file, chat):
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)",
            ("session", chat, owner, "room", "active", (datetime.now(UTC) + timedelta(minutes=10)).isoformat(), "{}"),
        )
        db.commit()
    return {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}


def start_turn(client, headers, request_id="next", text="How old am I?"):
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": text})
    assert response.status_code == 200, response.text
    return response.json()["context"]


def blocks(client, chat, **params):
    response = client.get(f"/v1/conversations/{chat}/context/blocks", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def setup(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def retraction_messages(context):
    return [m for m in context["messages"] if "Memory (untrusted memory data, id=retraction:" in m["content"]]


def test_hidden_memory_with_restating_turns_adds_retraction_block(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_turn(file, chat, "src", "user", "I am 26 years old", "2026-11-12T00:00:01+00:00")
        add_memory(file, chat, "age", "I am 26 years old", status="hidden", source_turn="src")
        add_turn(file, chat, "again1", "user", "Remember, I am 26 years old", "2026-11-12T00:00:02+00:00")
        add_turn(file, chat, "again2", "user", "As I said, I am 26", "2026-11-12T00:00:03+00:00")
        headers = add_session(file, chat)

        context = start_turn(client, headers)

        # The restating turns are dropped with the hidden memory (#194), and so is its source turn ...
        assert {"again1", "again2", "src"}.isdisjoint(context["included_record_ids"])
        # ... the retraction block stays as a generic, non-privileged reminder that never quotes the fact.
        found = retraction_messages(context)
        assert len(found) == 1
        assert found[0]["role"] == "user"
        assert "do not say what it was" in found[0]["content"].lower()
        assert "26" not in found[0]["content"]
        assert "retraction:age" in context["included_record_ids"]
        assert "retraction:age" in context["manifest"]["included_record_ids"]
        assert context["messages"][0]["role"] == "system"
        assert "do not use, repeat" not in context["messages"][0]["content"]


def test_retraction_scope_unhide_deleted_and_other_conversation(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        other = client.post("/v1/conversations").json()["id"]
        add_memory(file, chat, "deleted", "", status="deleted")
        add_memory(file, other, "elsewhere", "I like tea", status="hidden")
        add_memory(file, chat, "active", "I like jazz")
        headers = add_session(file, chat)
        assert retraction_messages(start_turn(client, headers, "r1")) == []

        add_memory(file, chat, "age", "I am 26 years old", status="hidden")
        assert len(retraction_messages(start_turn(client, headers, "r2"))) == 1
        sql(file, "UPDATE memories SET status='active' WHERE id='age'")
        assert retraction_messages(start_turn(client, headers, "r3")) == []


def test_retraction_is_bounded_and_survives_later_reply_dependency(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        for number in range(8):
            add_memory(file, chat, f"m{number}", "x" * 1000, status="hidden")
        headers = add_session(file, chat)
        first = start_turn(client, headers, "r1")
        found = retraction_messages(first)
        assert len(found) == 5
        assert all(len(m["content"]) < 700 for m in found)
        # An assistant reply derived from a turn that included retraction ids must not fail the closure.
        turn_id = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "r2", "text": "hi"}
        ).json()["turn_id"]
        reply = client.post(
            "/internal/agent/turn/finalize",
            headers=headers,
            json={"turn_id": turn_id, "text": "Hello there", "status": "completed"},
        )
        assert reply.status_code == 200, reply.text
        later = start_turn(client, headers, "r3")
        assert "Hello there" in json.dumps(later["messages"])


def test_blocks_endpoint_states_counts_and_no_writes(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_turn(file, chat, "src", "user", "I am 26 years old", "2026-11-12T00:00:01+00:00")
        add_memory(file, chat, "age", "I am 26 years old", status="hidden", source_turn="src")
        add_memory(file, chat, "gone", "", status="deleted")
        add_memory(file, chat, "tea", "I like tea")
        add_memory(file, chat, "jazz", "I like jazz")
        add_turn(
            file, chat, "reply-src", "user", "Tell me", "2026-11-12T00:00:02+00:00", {"included_record_ids": ["age"]}
        )
        add_turn(
            file,
            chat,
            "derived",
            "assistant",
            "You are 26",
            "2026-11-12T00:00:03+00:00",
            {"derivation": {"turn_id": "reply-src", "record_ids": ["age"]}},
            request="reply:reply-src",
        )
        for number in range(25):
            add_turn(file, chat, f"t{number:02d}", "user", f"line {number}", f"2026-11-12T01:00:{number:02d}+00:00")
        tables = ("turns", "memories", "context_exclusions", "context_operations", "context_revisions")
        before = count_rows(file, tables)

        body = blocks(client, chat, limit=100)

        by_id = {b["id"]: b for b in body["blocks"]}
        assert by_id["retraction:age"]["kind"] == "retraction"
        assert by_id["retraction:age"]["state"] == "included"
        assert by_id["age"]["state"] == "blocked" and by_id["age"]["reason"] == "hidden"
        assert by_id["gone"]["reason"] == "deleted"
        assert by_id["tea"]["state"] == "included" and by_id["tea"]["kind"] == "memory"
        assert by_id["src"]["state"] == "blocked" and by_id["src"]["reason"] == "source_of_blocked_memory"
        assert by_id["derived"]["state"] == "blocked"
        assert by_id["t24"]["state"] == "included" and by_id["t24"]["role"] == "user"
        assert by_id["t00"]["state"] == "outside_window"
        counts = body["counts"]
        assert counts["total"] == len(body["blocks"])
        assert counts["included"] == sum(1 for b in body["blocks"] if b["state"] == "included")
        assert counts["outside_window"] >= 1 and counts["blocked"] >= 4
        assert body["included_tokens_estimate"] == sum(
            b["estimated_tokens"] for b in body["blocks"] if b["state"] == "included"
        )
        assert "estimate" in body["token_estimate_method"]
        assert "system" not in {b["role"] for b in body["blocks"]}
        assert "Rules" not in json.dumps(body)

        after = count_rows(file, tables)
        assert after == before


def test_blocks_conversation_exclusion_and_tool_results(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "trip", "Trip to Tripoli")
        add_memory(file, chat, "hotel", "Hotel in Rome")
        add_turn(
            file, chat, "tool-turn", "user", "weather", "2026-11-12T00:00:01+00:00", {"included_record_ids": ["system"]}
        )
        sql(
            file,
            "INSERT INTO tool_calls (id,turn_id,request_id,name,state,result) "
            "VALUES ('call1','tool-turn','call1','get_current_weather','completed',?)",
            (json.dumps({"city": "Tripoli"}),),
        )
        proposal = client.post(f"/v1/conversations/{chat}/context/exclusions", json={"memory_ids": ["trip"]}).json()
        applied = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={"proposal": proposal["proposal"], "expected_revision": proposal["revision"], "idempotency_key": "k1"},
        )
        assert applied.status_code == 200, applied.text

        by_id = {b["id"]: b for b in blocks(client, chat)["blocks"]}

        assert by_id["trip"]["state"] == "excluded"
        assert by_id["hotel"]["state"] == "included"
        assert by_id["call1"]["kind"] == "tool_result" and by_id["call1"]["state"] == "included"


def test_blocks_bounded_paginated_truncated_and_owner_isolated(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "long", "y" * 1000)
        for number in range(12):
            add_turn(file, chat, f"t{number:02d}", "user", f"line {number}", f"2026-11-12T01:00:{number:02d}+00:00")

        first = blocks(client, chat, limit=5)
        second = blocks(client, chat, limit=5, offset=5)

        assert len(first["blocks"]) == 5 and first["has_more"] is True
        assert not {b["id"] for b in first["blocks"]} & {b["id"] for b in second["blocks"]}
        assert first["counts"]["total"] == 13
        long = next(b for b in blocks(client, chat)["blocks"] if b["id"] == "long")
        assert long["truncated"] is True and len(long["text"]) == 400 and long["estimated_tokens"] == 250
        assert client.get(f"/v1/conversations/{chat}/context/blocks", params={"limit": 101}).status_code == 422

    with TestClient(create_app(settings)) as other:
        other.post("/v1/demo-session")
        assert other.get(f"/v1/conversations/{chat}/context/blocks").status_code == 404
