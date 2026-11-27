"""The memory panel's scope read and the owner-scoped pending proposal (#ux-3)."""

import json
import sqlite3
from contextlib import closing

from app.domain.context_operations import sign_proposal, verify_proposal
from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder
from test_memory_capture import add_turn_session, configured, memory_rows, set_mode, turn

PROPOSAL = "/v1/conversations/{chat}/memory-proposal"
DECISION = "/v1/conversations/{chat}/memory-proposal/decision"


def stamp():
    return "2026-11-27T00:00:00+00:00"


def owner_of(file, conversation_id):
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT owner_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0]


def seed_temporary(file, conversation_id, memory_id, content):
    with closing(sqlite3.connect(file)) as db, db:
        owner = owner_of(file, conversation_id)
        db.execute(
            "INSERT INTO memories (id,owner_id,content,scope,embedding_model,source_conversation_id,version,status,"
            "created_at,updated_at) VALUES (?,?,?,'conversation',?,?,1,'active',?,?)",
            (memory_id, owner, content, "text-embedding-3-small", conversation_id, stamp(), stamp()),
        )
        db.commit()


def end_sessions(file, conversation_id):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute("UPDATE voice_sessions SET status='ended' WHERE conversation_id=?", (conversation_id,))
        db.commit()


def arm(client, file, chat, owner, text, request_id):
    headers = add_turn_session(file, chat, owner, identifier=f"session-{request_id}")
    body = turn(client, headers, text, request_id).json()
    proposal = body["memory_proposal"]
    assert proposal is not None, body
    return proposal


def decide(client, chat, proposal_id, decision):
    return client.post(DECISION.format(chat=chat), json={"proposal_id": proposal_id, "decision": decision})


def expire_proposal(file, confirmation_id, settings):
    with closing(sqlite3.connect(file)) as db, db:
        stored = db.execute(
            "SELECT result FROM memory_write_requests WHERE request_key=?", (confirmation_id,)
        ).fetchone()[0]
        result = json.loads(stored)
        payload = verify_proposal(result["proposal"]["token"], settings.context_proposal_secret)
        payload["exp"] = 1
        result["proposal"]["token"] = sign_proposal(payload, settings.context_proposal_secret)
        db.execute(
            "UPDATE memory_write_requests SET result=? WHERE request_key=?",
            (json.dumps(result), confirmation_id),
        )
        db.commit()


def test_list_reports_scope_and_filters_global_temporary_and_conversation(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        sibling = client.post("/v1/conversations").json()["id"]
        created = client.post(
            f"/v1/conversations/{chat}/memories",
            json={"content": "I prefer green tea", "idempotency_key": "global-1"},
        )
        assert created.status_code == 201, created.text
        seed_temporary(file, chat, "temp-chat", "Only this chat's note")
        seed_temporary(file, sibling, "temp-sibling", "Another chat's note")

        listed = client.get("/v1/memories").json()
        assert {item["id"]: item["scope"] for item in listed["items"]} == {
            created.json()["id"]: "global",
            "temp-chat": "conversation",
            "temp-sibling": "conversation",
        }
        global_only = client.get("/v1/memories", params={"scope": "global"}).json()
        assert {item["id"] for item in global_only["items"]} == {created.json()["id"]}
        temporary = client.get("/v1/memories", params={"scope": "conversation"}).json()
        assert {item["id"] for item in temporary["items"]} == {"temp-chat", "temp-sibling"}

        this_chat = client.get("/v1/memories", params={"scope": "conversation", "conversation_id": chat}).json()
        assert {item["id"] for item in this_chat["items"]} == {"temp-chat"}
        assert this_chat["total"] == 1
        sibling_chat = client.get("/v1/memories", params={"scope": "conversation", "conversation_id": sibling}).json()
        assert {item["id"] for item in sibling_chat["items"]} == {"temp-sibling"}
        assert client.get("/v1/memories", params={"scope": "everything"}).status_code == 422

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            assert other.get("/v1/memories").json()["items"] == []
            assert (
                other.get("/v1/memories", params={"scope": "conversation", "conversation_id": chat}).status_code == 404
            )
            assert (
                other.get("/v1/memories", params={"conversation_id": chat, "scope": "conversation"}).status_code == 404
            )

        assert client.delete(f"/v1/conversations/{chat}").status_code == 204
        gone = client.get("/v1/memories", params={"scope": "conversation", "conversation_id": chat})
        assert gone.status_code == 404
        assert gone.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"


def test_pending_proposal_is_null_then_the_armed_fact_and_owner_scoped(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        assert client.get(PROPOSAL.format(chat=chat)).json() == {"pending": None}

        proposal = arm(client, file, chat, owner, "I prefer tea with mint", "fact-1")
        pending = client.get(PROPOSAL.format(chat=chat)).json()["pending"]
        assert pending["id"] == proposal["confirmation_id"]
        assert pending["fact"] == "I prefer tea with mint"
        assert pending["expires_at"]

        sibling = client.post("/v1/conversations").json()["id"]
        assert client.get(PROPOSAL.format(chat=sibling)).json() == {"pending": None}

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            assert other.get(PROPOSAL.format(chat=chat)).status_code == 404
            assert decide(other, chat, proposal["confirmation_id"], "save").status_code == 404
            assert other.get(PROPOSAL.format(chat=sibling)).status_code == 404
            assert decide(other, sibling, proposal["confirmation_id"], "save").status_code == 404

        end_sessions(file, chat)
        assert client.delete(f"/v1/conversations/{chat}").status_code == 204
        assert client.get(PROPOSAL.format(chat=chat)).status_code == 404


def test_stale_proposal_id_never_saves_a_newer_fact(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        older = arm(client, file, chat, owner, "I prefer tea with mint", "fact-1")
        newer = arm(client, file, chat, owner, "I live in Benghazi", "fact-2")
        assert client.get(PROPOSAL.format(chat=chat)).json()["pending"]["id"] == newer["confirmation_id"]

        saved = decide(client, chat, older["confirmation_id"], "save")
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "saved"
        assert [row[0] for row in memory_rows(file)] == ["I prefer tea with mint"]
        pending = client.get(PROPOSAL.format(chat=chat)).json()["pending"]
        assert pending["id"] == newer["confirmation_id"]
        assert pending["fact"] == "I live in Benghazi"


def test_decision_requires_the_same_owner_and_chat_proposal(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        sibling = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        proposal = arm(client, file, chat, owner, "I prefer tea with mint", "fact-1")

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            other_chat = other.post("/v1/conversations").json()["id"]
            cross_owner = decide(other, other_chat, proposal["confirmation_id"], "save")
            assert cross_owner.status_code == 404
            assert cross_owner.json()["detail"]["code"] == "MEMORY_PROPOSAL_NOT_FOUND"
            assert memory_rows(file) == []

        cross_chat = decide(client, sibling, proposal["confirmation_id"], "save")
        assert cross_chat.status_code == 404
        assert cross_chat.json()["detail"]["code"] == "MEMORY_PROPOSAL_NOT_FOUND"
        assert memory_rows(file) == []

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            assert decide(other, chat, proposal["confirmation_id"], "save").status_code == 404
            assert memory_rows(file) == []


def test_decision_saves_once_replays_and_declines(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        first = arm(client, file, chat, owner, "I prefer tea with mint", "fact-1")

        saved = decide(client, chat, first["confirmation_id"], "save")
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "saved"
        assert [row[0] for row in memory_rows(file)] == ["I prefer tea with mint"]
        assert client.get(PROPOSAL.format(chat=chat)).json() == {"pending": None}

        replay = decide(client, chat, first["confirmation_id"], "save")
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "saved" and replay.json()["idempotent"] is True
        assert [row[0] for row in memory_rows(file)] == ["I prefer tea with mint"]

        second = arm(client, file, chat, owner, "I live in Benghazi", "fact-2")
        declined = decide(client, chat, second["confirmation_id"], "decline")
        assert declined.status_code == 200, declined.text
        assert declined.json()["status"] == "discarded"
        assert [row[0] for row in memory_rows(file)] == ["I prefer tea with mint"]

        replay_decline = decide(client, chat, second["confirmation_id"], "decline")
        assert replay_decline.status_code == 200, replay_decline.text
        assert replay_decline.json()["status"] == "discarded" and replay_decline.json()["idempotent"] is True
        assert [row[0] for row in memory_rows(file)] == ["I prefer tea with mint"]
        assert decide(client, chat, second["confirmation_id"], "maybe").status_code == 422


def test_a_declined_proposal_cannot_be_saved_by_replay(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        proposal = arm(client, file, chat, owner, "I prefer tea with mint", "fact-1")

        assert decide(client, chat, proposal["confirmation_id"], "decline").status_code == 200
        saved = decide(client, chat, proposal["confirmation_id"], "save")
        assert saved.status_code == 409
        assert saved.json()["detail"]["code"] == "MEMORY_PROPOSAL_RESOLVED"
        assert memory_rows(file) == []


def test_an_expired_proposal_is_not_pending_and_cannot_be_saved(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        proposal = arm(client, file, chat, owner, "I prefer tea with mint", "fact-1")
        expire_proposal(file, proposal["confirmation_id"], settings)

        assert client.get(PROPOSAL.format(chat=chat)).json() == {"pending": None}
        refused = decide(client, chat, proposal["confirmation_id"], "save")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "PROPOSAL_EXPIRED"
        assert memory_rows(file) == []


def test_decision_rechecks_the_conversation_memory_mode(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        proposal = arm(client, file, chat, owner, "I prefer tea with mint", "fact-1")
        assert client.put(f"/v1/conversations/{chat}/memory-mode", json={"mode": "off"}).status_code == 200

        refused = decide(client, chat, proposal["confirmation_id"], "save")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "MEMORY_OFF"
        assert memory_rows(file) == []

        declined = decide(client, chat, proposal["confirmation_id"], "decline")
        assert declined.status_code == 200 and declined.json()["status"] == "discarded"
        assert memory_rows(file) == []
