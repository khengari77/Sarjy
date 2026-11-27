"""The memory panel's scope read and the owner-scoped pending proposal (#ux-3)."""

import json
import sqlite3
from contextlib import closing

import pytest
from app.domain.context_operations import sign_proposal, verify_proposal
from app.domain.memory_capture import parse_capture
from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder
from test_memory_capture import add_turn_session, configured, memory_rows, set_mode, turn

PROPOSAL = "/v1/conversations/{chat}/memory-proposal"
DECISION = "/v1/conversations/{chat}/memory-proposal/decision"
CONFIRM = "/internal/agent/memory/confirm"
PROPOSE = "/internal/agent/tool/memory-propose"


def stamp():
    return "2026-11-27T00:00:00+00:00"


def owner_of(file, conversation_id):
    with closing(sqlite3.connect(file)) as db:
        return db.execute("SELECT owner_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0]


def seed_temporary(file, conversation_id, memory_id, content, subject=None):
    with closing(sqlite3.connect(file)) as db, db:
        owner = owner_of(file, conversation_id)
        db.execute(
            "INSERT INTO memories (id,owner_id,content,subject,scope,embedding_model,source_conversation_id,version,"
            "status,created_at,updated_at) VALUES (?,?,?,?,'conversation',?,?,1,'active',?,?)",
            (
                memory_id,
                owner,
                content,
                subject,
                "text-embedding-3-small",
                conversation_id,
                stamp(),
                stamp(),
            ),
        )
        db.commit()


def memory_records(file):
    with closing(sqlite3.connect(file)) as db:
        return db.execute(
            "SELECT content, source_conversation_id, scope, status, version FROM memories ORDER BY created_at"
        ).fetchall()


def end_sessions(file, conversation_id):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute("UPDATE voice_sessions SET status='ended' WHERE conversation_id=?", (conversation_id,))
        db.commit()


def arm_for(client, file, chat, owner, text, request_id):
    headers = add_turn_session(file, chat, owner, identifier=f"session-{request_id}")
    body = turn(client, headers, text, request_id).json()
    proposal = body["memory_proposal"]
    assert proposal is not None, body
    return proposal, headers


def arm(client, file, chat, owner, text, request_id):
    proposal, _ = arm_for(client, file, chat, owner, text, request_id)
    return proposal


def decide(client, chat, proposal_id, decision, **extra):
    return client.post(
        DECISION.format(chat=chat),
        json={"proposal_id": proposal_id, "decision": decision, **extra},
    )


def spoken_confirm(client, headers, token, decision="save"):
    return client.post(CONFIRM, headers=headers, json={"token": token, "decision": decision})


def create_global(client, chat, content, key):
    response = client.post(f"/v1/conversations/{chat}/memories", json={"content": content, "idempotency_key": key})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def propose_tool(client, file, chat, owner, text, request_id):
    headers = add_turn_session(file, chat, owner, identifier=f"session-{request_id}")
    turn_id = turn(client, headers, "hello there", request_id).json()["turn_id"]
    response = client.post(
        PROPOSE,
        headers=headers,
        json={"turn_id": turn_id, "request_id": f"{request_id}-p", "fact": text},
    )
    return response


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


def forge_proposal_text(file, confirmation_id, settings, text):
    """Re-sign an already recorded proposal as ``text`` (a credential never yields one through the app)."""
    capture = parse_capture(text)
    assert capture is not None, text
    with closing(sqlite3.connect(file)) as db, db:
        stored = db.execute(
            "SELECT result FROM memory_write_requests WHERE request_key=?", (confirmation_id,)
        ).fetchone()[0]
        result = json.loads(stored)
        proposal = result["proposal"]
        payload = verify_proposal(proposal["token"], settings.context_proposal_secret)
        payload.update(
            content=capture.text,
            subject=capture.subject,
            value=capture.value,
            explicit=capture.explicit,
            replaces=capture.replaces,
            negated=capture.negated,
        )
        proposal["token"] = sign_proposal(payload, settings.context_proposal_secret)
        proposal["text"] = capture.text
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
        assert pending["replaces"] is None

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


def test_cross_chat_temporary_contradiction_is_neither_quoted_nor_blocking(tmp_path):
    for label in ("decision", "spoken"):
        folder = tmp_path / label
        folder.mkdir()
        file, settings = configured(folder)
        with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
            owner = client.post("/v1/demo-session").json()["id"]
            chat_a = client.post("/v1/conversations").json()["id"]
            seed_temporary(file, chat_a, "temp-a-name", "My name is Sam", subject="attribute:name")

            chat_b = client.post("/v1/conversations").json()["id"]
            proposal, headers = arm_for(client, file, chat_b, owner, "My name is Alex", label)
            if label == "decision":
                saved = decide(client, chat_b, proposal["confirmation_id"], "save")
            else:
                saved = spoken_confirm(client, headers, proposal["token"], "save")
            assert saved.status_code == 200, saved.text
            assert saved.json()["status"] == "saved"
            assert "My name is Sam" not in saved.text
            assert ("My name is Alex", chat_b, "global", "active", 1) in memory_records(file)
            assert ("My name is Sam", chat_a, "conversation", "active", 1) in memory_records(file)


@pytest.mark.parametrize("mode", ["ask", "auto"])
def test_cross_chat_temporary_exact_duplicate_does_not_suppress_a_new_memory(tmp_path, mode):
    for label in ("decision", "spoken"):
        folder = tmp_path / f"{label}-{mode}"
        folder.mkdir()
        file, settings = configured(folder)
        with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
            owner = client.post("/v1/demo-session").json()["id"]
            set_mode(file, owner, mode)
            chat_a = client.post("/v1/conversations").json()["id"]
            seed_temporary(file, chat_a, "temp-a-location", "I live in Benghazi", subject="location")

            chat_b = client.post("/v1/conversations").json()["id"]
            proposal, headers = arm_for(client, file, chat_b, owner, "I live in Benghazi", f"{label}-{mode}")
            if label == "decision":
                saved = decide(client, chat_b, proposal["confirmation_id"], "save")
            else:
                saved = spoken_confirm(client, headers, proposal["token"], "save")
            assert saved.status_code == 200, saved.text
            assert saved.json()["status"] == "saved"
            assert ("I live in Benghazi", chat_b, "global", "active", 1) in memory_records(file)
            assert ("I live in Benghazi", chat_a, "conversation", "active", 1) in memory_records(file)


def test_auto_save_ignores_other_chat_temporary_duplicate_but_honours_global(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        set_mode(file, owner, "auto")
        chat_a = client.post("/v1/conversations").json()["id"]
        seed_temporary(file, chat_a, "temp-a-location", "I live in Benghazi", subject="location")

        chat_b = client.post("/v1/conversations").json()["id"]
        auto_saved = propose_tool(client, file, chat_b, owner, "I live in Benghazi", "auto-b")
        assert auto_saved.status_code == 200, auto_saved.text
        assert auto_saved.json()["status"] == "saved"
        assert ("I live in Benghazi", chat_b, "global", "active", 1) in memory_records(file)

        create_global(client, chat_a, "I live in Tripoli", "global-tripoli")
        chat_c = client.post("/v1/conversations").json()["id"]
        blocked = propose_tool(client, file, chat_c, owner, "I live in Tripoli", "auto-c")
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["status"] == "rejected"
        assert blocked.json()["error"] == "MEMORY_ALREADY_SAVED"
        assert not [row for row in memory_records(file) if row[0] == "I live in Tripoli" and row[1] == chat_c]


def test_decision_replace_supersedes_a_visible_fact_and_replays(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        old_id = create_global(client, chat, "My name is Sam", "old-name")
        proposal = arm(client, file, chat, owner, "My name is Alex", "replace-1")

        pending = client.get(PROPOSAL.format(chat=chat)).json()["pending"]
        assert pending["replaces"] == {"id": old_id, "version": 1, "content": "My name is Sam"}

        replaced = decide(client, chat, proposal["confirmation_id"], "replace", target_id=old_id, target_version=1)
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["status"] == "corrected"
        assert replaced.json()["memory"]["supersedes_id"] == old_id
        records = memory_records(file)
        assert ("My name is Sam", chat, "global", "superseded", 1) in records
        assert len([row for row in records if row[0] == "My name is Alex" and row[3] == "active"]) == 1
        assert client.get(PROPOSAL.format(chat=chat)).json() == {"pending": None}

        replay = decide(client, chat, proposal["confirmation_id"], "replace", target_id=old_id, target_version=1)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "corrected" and replay.json()["idempotent"] is True
        assert len([row for row in memory_records(file) if row[0] == "My name is Alex" and row[3] == "active"]) == 1


def test_save_on_a_conflicting_proposal_still_requires_clarification(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        create_global(client, chat, "I live in Benghazi", "old-location")
        proposal = arm(client, file, chat, owner, "I live in Tripoli", "conflict-1")

        refused = decide(client, chat, proposal["confirmation_id"], "save")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "MEMORY_CLARIFICATION_REQUIRED"
        records = memory_records(file)
        assert ("I live in Benghazi", chat, "global", "active", 1) in records
        assert not [row for row in records if row[0] == "I live in Tripoli" and row[3] == "active"]


def test_invisible_other_chat_conflict_is_not_offered_as_a_replacement(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat_a = client.post("/v1/conversations").json()["id"]
        seed_temporary(file, chat_a, "temp-a-name", "My name is Sam", subject="attribute:name")

        chat_b = client.post("/v1/conversations").json()["id"]
        proposal = arm(client, file, chat_b, owner, "My name is Alex", "invisible-1")
        response = client.get(PROPOSAL.format(chat=chat_b))
        assert response.json()["pending"]["replaces"] is None
        assert "My name is Sam" not in response.text

        replaced = decide(
            client, chat_b, proposal["confirmation_id"], "replace", target_id="temp-a-name", target_version=1
        )
        assert replaced.status_code == 409
        assert replaced.json()["detail"]["code"] == "MEMORY_PROPOSAL_TARGET_CHANGED"
        assert ("My name is Sam", chat_a, "conversation", "active", 1) in memory_records(file)
        assert not [row for row in memory_records(file) if row[0] == "My name is Alex"]


def test_pending_replaces_a_visible_temporary_fact_in_the_same_chat(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        seed_temporary(file, chat, "temp-name", "My name is Sam", subject="attribute:name")
        arm(client, file, chat, owner, "My name is Alex", "temp-replace")

        pending = client.get(PROPOSAL.format(chat=chat)).json()["pending"]
        assert pending["replaces"] == {"id": "temp-name", "version": 1, "content": "My name is Sam"}


def test_decision_rechecks_credential_sensitivity(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        proposal = arm(client, file, chat, owner, "I prefer tea with mint", "credential-1")
        forge_proposal_text(file, proposal["confirmation_id"], settings, "my password is hunter2")

        pending = client.get(PROPOSAL.format(chat=chat)).json()["pending"]
        assert pending["fact"] == "my password is hunter2"
        refused = decide(client, chat, proposal["confirmation_id"], "save")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "MEMORY_SENSITIVE"
        assert memory_rows(file) == []


def test_erasing_the_saved_fact_scrubs_the_pending_proposal(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        proposal = arm(client, file, chat, owner, "I prefer tea with mint", "erase-1")
        saved = decide(client, chat, proposal["confirmation_id"], "save")
        assert saved.status_code == 200, saved.text
        memory_id = saved.json()["memory"]["id"]

        assert client.delete(f"/v1/memories/{memory_id}").status_code == 200
        assert client.get(PROPOSAL.format(chat=chat)).json() == {"pending": None}
        for decision in ("save", "decline"):
            refused = decide(client, chat, proposal["confirmation_id"], decision)
            assert refused.status_code == 404
            assert refused.json()["detail"]["code"] == "MEMORY_PROPOSAL_NOT_FOUND"
        assert all(row[2] != "active" for row in memory_rows(file))


@pytest.mark.parametrize("mode", ["ask", "auto"])
def test_decision_completes_in_ask_and_auto_mode(tmp_path, mode):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, mode)
        proposal = arm(client, file, chat, owner, "I prefer tea with mint", f"mode-{mode}")

        saved = decide(client, chat, proposal["confirmation_id"], "save")
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "saved"
        assert ("I prefer tea with mint", chat, "global", "active", 1) in memory_records(file)


def test_ask_mode_saves_a_sensitive_but_noncredential_fact(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        proposal = arm(client, file, chat, owner, "my salary is 5000", "sensitive-1")

        saved = decide(client, chat, proposal["confirmation_id"], "save")
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "saved"
        assert ("my salary is 5000", chat, "global", "active", 1) in memory_records(file)


def test_pending_proposal_requires_the_capture_secret(tmp_path):
    _, settings = configured(tmp_path)
    settings = settings.model_copy(update={"context_proposal_secret": "", "agent_service_secret": ""})
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]

        response = client.get(PROPOSAL.format(chat=chat))
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "MEMORY_CAPTURE_NOT_CONFIGURED"
        for decision in ("save", "decline", "replace"):
            extra = {"target_id": "unknown", "target_version": 1} if decision == "replace" else {}
            refused = decide(client, chat, "unknown-proposal", decision, **extra)
            assert refused.status_code == 503, refused.text
            assert refused.json()["detail"]["code"] == "MEMORY_CAPTURE_NOT_CONFIGURED"


def test_pending_replaces_uses_the_indexed_lookup_only(tmp_path, monkeypatch):
    from app.services import memory_capture

    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        shown = create_global(client, chat, "My name is Sam", "indexed-sam")
        arm(client, file, chat, owner, "My name is Alex", "indexed-alex")

        def legacy_scan(*args, **kwargs):
            raise AssertionError("pending must not run the legacy NULL-subject scan")

        monkeypatch.setattr(memory_capture, "_contradictions", legacy_scan)
        pending = client.get(PROPOSAL.format(chat=chat)).json()["pending"]
        assert pending["replaces"] == {"id": shown, "version": 1, "content": "My name is Sam"}


def test_replace_target_must_match_the_fact_the_user_was_shown(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        old_id = create_global(client, chat, "My name is Sam", "shown-sam")
        proposal = arm(client, file, chat, owner, "My name is Alex", "stale-target")
        assert client.get(PROPOSAL.format(chat=chat)).json()["pending"]["replaces"] == {
            "id": old_id,
            "version": 1,
            "content": "My name is Sam",
        }

        corrected = client.post(
            f"/v1/memories/{old_id}/correct",
            json={"content": "My name is Samir", "expected_version": 1, "idempotency_key": "correct-shown"},
        )
        assert corrected.status_code == 200, corrected.text

        refused = decide(client, chat, proposal["confirmation_id"], "replace", target_id=old_id, target_version=1)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "MEMORY_PROPOSAL_TARGET_CHANGED"
        records = memory_records(file)
        assert ("My name is Samir", chat, "global", "active", 2) in records
        assert not [row for row in records if row[0] == "My name is Alex"]


def test_replace_with_two_visible_conflicts_fails_closed(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        first = create_global(client, chat, "My name is Sam", "multi-sam")
        create_global(client, chat, "My name is Alex", "multi-alex")
        proposal = arm(client, file, chat, owner, "My name is Omar", "multi-omar")
        assert client.get(PROPOSAL.format(chat=chat)).json()["pending"]["replaces"] is None
        before = memory_records(file)

        refused = decide(client, chat, proposal["confirmation_id"], "replace", target_id=first, target_version=1)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "MEMORY_MULTIPLE_TARGETS"
        assert memory_records(file) == before


def test_replace_keeps_a_temporary_fact_temporary(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat_a = client.post("/v1/conversations").json()["id"]
        chat_b = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        seed_temporary(file, chat_a, "temp-sam", "My name is Sam", subject="attribute:name")
        proposal = arm(client, file, chat_a, owner, "My name is Alex", "temp-replace-decision")
        assert client.get(PROPOSAL.format(chat=chat_a)).json()["pending"]["replaces"] == {
            "id": "temp-sam",
            "version": 1,
            "content": "My name is Sam",
        }

        replaced = decide(
            client, chat_a, proposal["confirmation_id"], "replace", target_id="temp-sam", target_version=1
        )
        assert replaced.status_code == 200, replaced.text
        records = memory_records(file)
        assert ("My name is Sam", chat_a, "conversation", "superseded", 1) in records
        assert ("My name is Alex", chat_a, "conversation", "active", 2) in records

        visible_a = client.get(
            "/v1/memories",
            params={"scope": "conversation", "conversation_id": chat_a, "status": "active"},
        ).json()["items"]
        assert {item["content"] for item in visible_a} == {"My name is Alex"}
        visible_b = client.get("/v1/memories", params={"scope": "conversation", "conversation_id": chat_b}).json()[
            "items"
        ]
        assert visible_b == []
        assert client.get("/v1/memories", params={"scope": "global"}).json()["items"] == []


def test_legacy_correct_keeps_a_temporary_fact_temporary(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        client.post("/v1/demo-session")
        chat_a = client.post("/v1/conversations").json()["id"]
        chat_b = client.post("/v1/conversations").json()["id"]
        seed_temporary(file, chat_a, "temp-legacy", "My name is Sam", subject="attribute:name")

        corrected = client.post(
            "/v1/memories/temp-legacy/correct",
            json={"content": "My name is Samir", "expected_version": 1, "idempotency_key": "legacy-correct"},
        )
        assert corrected.status_code == 200, corrected.text
        records = memory_records(file)
        assert ("My name is Sam", chat_a, "conversation", "superseded", 1) in records
        assert ("My name is Samir", chat_a, "conversation", "active", 2) in records

        visible_a = client.get(
            "/v1/memories",
            params={"scope": "conversation", "conversation_id": chat_a, "status": "active"},
        ).json()["items"]
        assert {item["content"] for item in visible_a} == {"My name is Samir"}
        visible_b = client.get("/v1/memories", params={"scope": "conversation", "conversation_id": chat_b}).json()[
            "items"
        ]
        assert visible_b == []
        assert client.get("/v1/memories", params={"scope": "global"}).json()["items"] == []


def test_replace_is_stale_when_the_proposed_fact_already_exists(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings, embedding_adapter=FakeEmbedder())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        set_mode(file, owner, "ask")
        shown = create_global(client, chat, "My name is Sam", "stale-sam")
        create_global(client, chat, "My name is Alex", "stale-alex")
        proposal = arm(client, file, chat, owner, "My name is Alex", "stale-existing")
        assert client.get(PROPOSAL.format(chat=chat)).json()["pending"]["replaces"] == {
            "id": shown,
            "version": 1,
            "content": "My name is Sam",
        }
        before = memory_records(file)

        refused = decide(client, chat, proposal["confirmation_id"], "replace", target_id=shown, target_version=1)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "MEMORY_PROPOSAL_STALE"
        assert memory_records(file) == before
