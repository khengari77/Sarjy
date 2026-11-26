"""User-safe per-turn context receipt: snapshot projection, exclusions and diff."""

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.main import create_app as real_create_app
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st


class FakeEmbedder:
    async def embed(self, db, text):
        return (1.0,) + (0.0,) * 1535


def create_app(settings):
    return real_create_app(settings, embedding_adapter=FakeEmbedder())


def configured(tmp_path):
    file = tmp_path / "receipt.db"
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


def add_memory(file, chat, memory_id, content, status="active", source_turn=None):
    sql(
        file,
        "INSERT INTO memories (id,owner_id,content,embedding_model,source_turn_id,source_conversation_id,version,status,"
        "created_at,updated_at) VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), ?, 'text-embedding-3-small', "
        "?, ?, 1, ?, '2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
        (memory_id, chat, content, source_turn, chat, status),
    )


def add_session(file, chat, session_id="session", room="room"):
    with closing(sqlite3.connect(file)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)",
            (
                session_id,
                chat,
                owner,
                room,
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                "{}",
            ),
        )
        db.commit()
    return {"x-agent-secret": "test-service-secret", "x-session-id": session_id, "x-room-name": room}


def start_turn(client, headers, request_id, text="How old am I?"):
    response = client.post("/internal/agent/turn/start", headers=headers, json={"request_id": request_id, "text": text})
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["turn_id"], payload["context"]


def receipt(client, chat, turn_id):
    return client.get(f"/v1/conversations/{chat}/turns/{turn_id}/context")


def setup(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def snapshot_of(file, turn_id):
    with closing(sqlite3.connect(file)) as db:
        raw = db.execute("SELECT snapshot FROM turns WHERE id=?", (turn_id,)).fetchone()[0]
    return json.loads(raw)


def test_receipt_shape_truncation_and_prompt_hash(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        long_text = "L" * 1000
        add_memory(file, chat, "long", long_text)
        headers = add_session(file, chat)
        turn_id, context = start_turn(client, headers, "r1")

        response = receipt(client, chat, turn_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["turn_id"] == turn_id
        assert body["revision"] == context["revision"]
        assert body["prompt_sha256"] == context["canonical_prompt_sha256"][:12]
        assert len(body["prompt_sha256"]) == 12
        assert body["history_count"] == 0
        assert body["previous_turn_id"] is None
        assert body["budget"]["limit_chars"] == 16000
        assert body["budget"]["used_chars"] == sum(len(message["content"]) for message in context["messages"])
        assert body["diff"] == {"added": sorted(context["included_record_ids"]), "removed": []}
        assert all(entry["id"] != "system" for entry in body["included"])

        memory = next(entry for entry in body["included"] if entry["id"] == "long")
        assert memory["kind"] == "memory" and memory["trust"] == "data"
        assert memory["text"] == long_text[:400]


def test_included_excluded_split_never_leaks_excluded_text(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "visible", "I like jazz")
        add_memory(file, chat, "hidden", "hidden-secret-123", status="hidden")
        add_memory(file, chat, "deleted", "deleted-secret-456", status="deleted")
        add_memory(file, chat, "excluded", "excluded-secret-789", status="hidden")
        # Make the excluded record active again, then exclude it through the real route.
        sql(file, "UPDATE memories SET status='active' WHERE id='excluded'")
        proposal = client.post(f"/v1/conversations/{chat}/context/exclusions", json={"memory_ids": ["excluded"]})
        assert proposal.status_code == 200, proposal.text
        applied = client.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={
                "proposal": proposal.json()["proposal"],
                "expected_revision": proposal.json()["revision"],
                "idempotency_key": "exclude-1",
            },
        )
        assert applied.status_code == 200, applied.text

        headers = add_session(file, chat)
        turn_id, _ = start_turn(client, headers, "r1")
        body = receipt(client, chat, turn_id).json()
        included_ids = {entry["id"] for entry in body["included"]}
        assert "visible" in included_ids
        assert not included_ids & {"hidden", "deleted", "excluded"}
        assert not any(entry_id.startswith("retraction:") for entry_id in included_ids)
        reasons = {entry["id"]: entry["reason"] for entry in body["excluded"]}
        assert reasons["hidden"] == "hidden"
        assert reasons["deleted"] == "deleted"
        assert reasons["excluded"] == "excluded_in_conversation"
        assert all(set(entry) == {"id", "reason"} for entry in body["excluded"])

        serialised = json.dumps(body, ensure_ascii=False)
        for secret in ("hidden-secret-123", "deleted-secret-456", "excluded-secret-789"):
            assert secret not in serialised

        # Hiding an already-included memory must redact its old snapshot text too.
        sql(file, "UPDATE memories SET status='hidden' WHERE id='visible'")
        later = json.dumps(receipt(client, chat, turn_id).json(), ensure_ascii=False)
        assert "I like jazz" not in later


def test_receipt_never_contains_instructions_or_provider_configuration(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "age", "I am 26 years old")
        headers = add_session(file, chat)
        turn_id, _ = start_turn(client, headers, "r1")
        body = receipt(client, chat, turn_id).json()
        serialised = json.dumps(body, ensure_ascii=False)

        snapshot = snapshot_of(file, turn_id)
        instructions = snapshot["messages"][0]["content"]
        assert instructions
        assert instructions not in serialised
        assert "pipeline_config" not in serialised
        assert "model_request" not in serialised
        llm = snapshot["pipeline_config"].get("llm", {})
        for value in (llm.get("provider"), llm.get("model")):
            if value:
                assert value not in serialised


def test_receipt_is_owner_and_conversation_isolated(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        turn_id, _ = start_turn(client, headers, "r1")

        other_chat = client.post("/v1/conversations").json()["id"]
        other_headers = add_session(file, other_chat, session_id="session-2", room="room-2")
        other_turn, _ = start_turn(client, other_headers, "r2", text="Another conversation")
        assert receipt(client, chat, other_turn).status_code == 404

        no_context_chat = client.post("/v1/conversations").json()["id"]
        sql(
            file,
            "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) "
            "VALUES ('bare',?,'bare','assistant','No snapshot','completed','2026-11-12T00:00:00+00:00',NULL)",
            (no_context_chat,),
        )
        bare = receipt(client, no_context_chat, "bare")
        assert bare.status_code == 404
        assert bare.json()["detail"]["code"] == "TURN_HAS_NO_CONTEXT"

    with TestClient(create_app(settings)) as other:
        other.post("/v1/demo-session")
        assert receipt(other, chat, turn_id).status_code == 404


def test_diff_against_previous_user_turn_after_save_and_hide(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "base", "I like tea")
        headers = add_session(file, chat)
        first, _ = start_turn(client, headers, "r1")

        add_memory(file, chat, "new", "I live in Benghazi")
        second, _ = start_turn(client, headers, "r2", text="Where do I live?")
        second_body = receipt(client, chat, second).json()
        assert second_body["previous_turn_id"] == first
        assert "new" in second_body["diff"]["added"]
        assert second in second_body["diff"]["added"]
        assert "base" not in second_body["diff"]["added"]
        assert "base" not in second_body["diff"]["removed"]
        by_id = {entry["id"]: entry for entry in second_body["included"]}
        assert by_id[first]["kind"] == "history" and by_id[first]["trust"] == "conversation"
        assert second_body["history_count"] == 1

        sql(file, "UPDATE memories SET status='hidden' WHERE id='new'")
        third, _ = start_turn(client, headers, "r3", text="Still?")
        third_body = receipt(client, chat, third).json()
        assert third_body["previous_turn_id"] == second
        assert "new" in third_body["diff"]["removed"]
        assert third in third_body["diff"]["added"]
        assert {"id": "new", "reason": "hidden"} in third_body["excluded"]


def test_assistant_turn_falls_back_to_the_preceding_user_snapshot(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        add_memory(file, chat, "age", "I am 26 years old")
        headers = add_session(file, chat)
        user_turn, context = start_turn(client, headers, "r1")
        reply = client.post(
            "/internal/agent/turn/finalize",
            headers=headers,
            json={"turn_id": user_turn, "text": "You are 26.", "status": "completed"},
        )
        assert reply.status_code == 200, reply.text
        assistant_turn = reply.json()["id"]

        response = receipt(client, chat, assistant_turn)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["turn_id"] == assistant_turn
        assert body["prompt_sha256"] == context["canonical_prompt_sha256"][:12]
        included_ids = {entry["id"] for entry in body["included"]}
        assert included_ids == {value for value in context["included_record_ids"] if value not in ("system", user_turn)}
        assert body["previous_turn_id"] is None


@given(
    st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=40).map(
            lambda value: f"hidden{value}"
        ),
        min_size=1,
        max_size=3,
        unique=True,
    )
)
@hypothesis_settings(max_examples=8, deadline=None)
def test_any_hidden_memory_content_never_appears(contents):
    with tempfile.TemporaryDirectory() as directory:
        file, settings = configured(Path(directory))
        with TestClient(create_app(settings)) as client:
            chat = setup(client)
            for index, content in enumerate(contents):
                add_memory(file, chat, f"hidden-{index}", content, status="hidden")
            headers = add_session(file, chat)
            turn_id, _ = start_turn(client, headers, "r1", text="What do you know?")
            body = receipt(client, chat, turn_id).json()
            serialised = json.dumps(body, ensure_ascii=False)
            for content in contents:
                assert content not in serialised


def test_a_turn_dropped_for_restating_a_forgotten_fact_says_so(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = setup(client)
        headers = add_session(file, chat)
        add_memory(file, chat, "home", "I live in Benghazi")
        restating, _ = start_turn(client, headers, "r0", text="Benghazi is where I live")
        sql(file, "UPDATE memories SET status='hidden' WHERE id='home'")
        later, _ = start_turn(client, headers, "r1", text="What is the weather like?")
        reasons = {entry["id"]: entry["reason"] for entry in receipt(client, chat, later).json()["excluded"]}
        assert reasons["home"] == "hidden"
        assert reasons[restating] == "restates_forgotten_fact"
        assert "Benghazi" not in json.dumps(receipt(client, chat, later).json())
