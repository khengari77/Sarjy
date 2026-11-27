"""Consent-first voice memory exclusion: lookup, proposal, confirm and Undo."""

import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta

import app.services.commands as commands_module
import pytest
from app.config import Settings
from app.domain.commands import (
    memory_request_is_global,
    memory_restore_requested,
    parse_exclusion_confirmation,
    parse_memory_exclusion_target,
)
from app.domain.context_operations import verify_proposal
from app.main import create_app
from app.services.memory_visibility import HIDE_OPERATION
from app.services.voice_exclusions import propose_exclusion
from fastapi.testclient import TestClient

SECRET = "test-proposal-secret"


@pytest.fixture
def legacy_forget(monkeypatch):
    """Forget now hides in one step; these tests cover the retained propose/confirm
    machinery (restore, consent one-shot, expiry, cross-owner) by routing forget to it."""

    def propose(db, *, owner_id, conversation_id, target, request_id, search):
        return propose_exclusion(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            target=target,
            request_id=request_id,
            secret=SECRET,
            search=search,
        )

    monkeypatch.setattr(commands_module, "hide_target", propose)


def configured(tmp_path, name="voice-memory"):
    file = tmp_path / f"{name}.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret=SECRET,
        _env_file=None,
    )
    return file, settings


def new_chat(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def voice(client, conversation, text, *, request, revision=0):
    return client.post(
        "/v1/actions",
        json={
            "conversation_id": conversation,
            "request_id": request,
            "expected_revision": revision,
            "source": "transcript",
            "transcript": text,
        },
    )


def seed(file, conversation_id, rows, owner_id=None, scope="global", status="active"):
    with closing(sqlite3.connect(file)) as db, db:
        owner = (
            owner_id or db.execute("SELECT owner_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()[0]
        )
        for record_id, content in rows:
            db.execute(
                "INSERT INTO memories (id,owner_id,content,scope,embedding_model,source_conversation_id,version,status,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,'2026-11-12T00:00:00+00:00',"
                "'2026-11-12T00:00:00+00:00')",
                (record_id, owner, content, scope, "text-embedding-3-small", conversation_id, status),
            )
        db.commit()


def seed_operation(
    file,
    *,
    operation_id,
    owner_id,
    conversation_id,
    command_type,
    created_at,
    payload=None,
    outcome="applied",
    revision_after=None,
):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO context_operations (id,owner_id,conversation_id,command_type,payload,revision_before,"
            "revision_after,idempotency_key,outcome,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                operation_id,
                owner_id,
                conversation_id,
                command_type,
                json.dumps(payload or {}),
                0,
                revision_after,
                f"idem-{operation_id}",
                outcome,
                created_at,
            ),
        )
        db.commit()


def seed_exclusion(file, *, exclusion_id, owner_id, conversation_id, memory_id, operation_id):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO context_exclusions (id,owner_id,conversation_id,memory_id,operation_id,scope,active,"
            "created_at) VALUES (?,?,?,?,?,'conversation',1,'2026-11-12T00:00:00+00:00')",
            (exclusion_id, owner_id, conversation_id, memory_id, operation_id),
        )
        db.commit()


def memory_status(file, memory_id):
    with closing(sqlite3.connect(file)) as db, db:
        return db.execute("SELECT status FROM memories WHERE id=?", (memory_id,)).fetchone()[0]


def exclusion_active(file, operation_id):
    with closing(sqlite3.connect(file)) as db, db:
        return bool(
            db.execute("SELECT active FROM context_exclusions WHERE operation_id=?", (operation_id,)).fetchone()[0]
        )


def seed_owner(file, owner_id):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO identities (id,token_hash,expires_at) VALUES (?,?,?)",
            (owner_id, f"hash-{owner_id}", (datetime.now(UTC) + timedelta(days=1)).isoformat()),
        )
        db.commit()


def counts(file):
    with closing(sqlite3.connect(file)) as db, db:
        revision = db.execute("SELECT revision FROM conversations").fetchone()[0]
        exclusions = db.execute("SELECT count(*) FROM context_exclusions").fetchone()[0]
        active = db.execute("SELECT count(*) FROM context_exclusions WHERE active=1").fetchone()[0]
        operations = db.execute("SELECT count(*) FROM context_operations").fetchone()[0]
    return revision, exclusions, active, operations


def latest_proposal(file, command_type="exclude_proposal"):
    with closing(sqlite3.connect(file)) as db, db:
        row = db.execute(
            "SELECT payload,outcome FROM context_operations WHERE command_type=? ORDER BY created_at DESC LIMIT 1",
            (command_type,),
        ).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), row[1]


def test_parsing_claims_only_the_newly_supported_phrasings():
    assert parse_memory_exclusion_target("Forget my name.") == "my name"
    assert parse_memory_exclusion_target("Exclude the Paris trip.") == "the Paris trip"
    assert parse_memory_exclusion_target("Forget the Paris trip but keep the budget.") is None
    assert parse_memory_exclusion_target("Delete the memory of the Paris trip.") is None
    assert parse_memory_exclusion_target("Forget it.") is None
    assert memory_request_is_global("Forget my name everywhere.") is True
    assert memory_request_is_global("Forget my name.") is False
    assert memory_restore_requested("Restore excluded memories.") is True
    assert memory_restore_requested("Bring back the memories I excluded.") is True
    assert parse_exclusion_confirmation("Yes, please!") is True
    assert parse_exclusion_confirmation("No thanks.") is False
    assert parse_exclusion_confirmation("Don't.") is False
    assert parse_exclusion_confirmation("yes but keep the budget") is None


@pytest.mark.usefixtures("legacy_forget")
def test_forget_my_name_proposes_signed_expiring_proposal_then_applies_once_and_undoes(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed_owner(file, "other-owner")
        seed(file, chat, [("name", "My name is Khengari"), ("city", "I live in Tripoli")])
        # Another owner's matching fact must never surface in this conversation.
        seed(file, chat, [("other-name", "My name is Intruder")], owner_id="other-owner")

        proposed = voice(client, chat, "Forget my name.", request="proposal-1")
        assert proposed.status_code == 200, proposed.text
        result = proposed.json()["result"]
        assert result["kind"] == "needs_confirmation"
        assert result["detail"] == "MEMORY_EXCLUSION_PROPOSED"
        assert result["changed"] is False
        assert result["spoken"] == "Exclude 'My name is Khengari' from this conversation? Say yes to exclude, or no."
        assert counts(file) == (0, 0, 0, 1), "a proposal must not exclude or advance the revision"

        pending, outcome = latest_proposal(file)
        assert outcome == "proposed"
        payload = verify_proposal(pending["token"], SECRET)
        with closing(sqlite3.connect(file)) as db, db:
            owner_id = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        assert payload["owner_id"] == owner_id
        assert payload["conversation_id"] == chat
        assert payload["revision"] == 0
        assert payload["excluded_ids"] == ["name"]
        assert payload["exp"] > int(time.time())

        confirmed = voice(client, chat, "Yes.", request="confirm-1")
        applied = confirmed.json()["result"]
        assert applied["kind"] == "executed"
        assert applied["detail"] == "MEMORY_EXCLUDED"
        assert applied["changed"] is True
        assert applied["operation_id"]
        assert applied["spoken"] == (
            "Excluded 'My name is Khengari' from this conversation. Say undo that exclusion to restore it."
        )
        assert counts(file) == (1, 1, 1, 2)
        assert client.get(f"/v1/conversations/{chat}/context").json()["excluded_ids"] == ["name"]

        replay = voice(client, chat, "Yes.", request="confirm-1")
        assert replay.json() == confirmed.json()
        assert counts(file) == (1, 1, 1, 2), "replayed confirmation must not double-apply"

        restore = voice(client, chat, "Undo that exclusion.", request="restore-1", revision=1)
        restore_result = restore.json()["result"]
        assert restore_result["kind"] == "needs_confirmation"
        assert restore_result["detail"] == "MEMORY_RESTORE_PROPOSED"
        assert restore_result["spoken"] == (
            "Restore 'My name is Khengari' to this conversation? Say yes to restore, or no."
        )
        assert counts(file) == (1, 1, 1, 3)

        restored = voice(client, chat, "Yes.", request="restore-confirm-1", revision=1).json()["result"]
        assert restored["kind"] == "executed"
        assert restored["detail"] == "MEMORY_RESTORED"
        assert restored["operation_id"]
        assert restored["spoken"] == "Restored the memory to this conversation."
        assert counts(file) == (2, 1, 0, 4)
        assert client.get(f"/v1/conversations/{chat}/context").json()["excluded_ids"] == []

        replay_restore = voice(client, chat, "Yes.", request="restore-confirm-1", revision=1)
        assert replay_restore.json()["result"] == restored
        assert counts(file) == (2, 1, 0, 4)


def test_zero_matches_is_honest_and_many_matches_ask_one_clarification(tmp_path):
    file, settings = configured(tmp_path, "zero")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("city", "I live in Tripoli")])
        result = voice(client, chat, "Forget my name.", request="missing").json()["result"]
        assert result["kind"] == "unchanged"
        assert result["detail"] == "MEMORY_TARGET_NOT_FOUND"
        assert result["spoken"] == "I couldn't find anything matching my name; nothing was changed."
        assert counts(file) == (0, 0, 0, 0)

    file, settings = configured(tmp_path, "many")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("name-a", "My name is A"), ("name-b", "My name is B")])
        result = voice(client, chat, "Forget my name.", request="ambiguous").json()["result"]
        assert result["kind"] == "needs_confirmation"
        assert result["detail"] == "MEMORY_TARGET_AMBIGUOUS"
        assert "I have two matches:" in result["spoken"]
        assert "My name is A" in result["spoken"] and "My name is B" in result["spoken"]
        assert "Which one?" in result["spoken"]
        assert counts(file) == (0, 0, 0, 0), "an ambiguous request must not propose an exclusion"

        # "Yes" without a pending proposal is ordinary conversation, never a
        # blanket exclusion of every candidate.
        assert voice(client, chat, "Yes.", request="stray-yes").json() == {"handled": False}
        assert counts(file) == (0, 0, 0, 0)


@pytest.mark.usefixtures("legacy_forget")
def test_no_discards_the_proposal_and_leaves_context_untouched(tmp_path):
    file, settings = configured(tmp_path, "decline")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("name", "My name is Khengari")])
        voice(client, chat, "Forget my name.", request="proposal-1")

        declined = voice(client, chat, "No thanks.", request="decline-1").json()["result"]
        assert declined["kind"] == "unchanged"
        assert declined["detail"] == "MEMORY_CHANGE_DECLINED"
        assert declined["spoken"] == "Okay, I won't change anything."
        assert counts(file) == (0, 0, 0, 1)

        replay = voice(client, chat, "No thanks.", request="decline-1")
        assert replay.json()["result"] == declined
        assert counts(file) == (0, 0, 0, 1)

        assert voice(client, chat, "Yes.", request="late-yes").json() == {"handled": False}
        assert counts(file) == (0, 0, 0, 1)


@pytest.mark.usefixtures("legacy_forget")
def test_expired_proposal_is_refused_without_applying(tmp_path, monkeypatch):
    import app.services.voice_exclusions as voice_exclusions

    monkeypatch.setattr(voice_exclusions, "TTL_SECONDS", -1)
    file, settings = configured(tmp_path, "expired")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("name", "My name is Khengari")])
        proposed = voice(client, chat, "Forget my name.", request="proposal-1").json()["result"]
        assert proposed["kind"] == "needs_confirmation"

        result = voice(client, chat, "Yes.", request="confirm-1").json()["result"]
        assert result["kind"] == "unchanged"
        assert result["detail"] == "PROPOSAL_EXPIRED"
        assert result["spoken"] == "That request expired. Nothing was changed."
        assert counts(file) == (0, 0, 0, 1)


def test_global_and_other_destructive_phrasings_still_refuse_honestly(tmp_path):
    file, settings = configured(tmp_path, "refusals")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("name", "My name is Khengari")])

        global_result = voice(client, chat, "Forget my name everywhere.", request="global").json()["result"]
        assert global_result["kind"] == "unsupported"
        assert global_result["detail"] == "MEMORY_GLOBAL_RETRACTION_UNAVAILABLE"
        assert "every conversation" in global_result["spoken"]
        assert "Nothing was changed" in global_result["spoken"]
        assert counts(file) == (0, 0, 0, 0)

        compound = voice(client, chat, "Forget the Paris trip but keep the budget.", request="compound").json()[
            "result"
        ]
        assert compound["kind"] == "unsupported"
        assert compound["detail"] == "MEMORY_MUTATION_UNSUPPORTED"
        assert counts(file) == (0, 0, 0, 0)

        deleted = voice(client, chat, "Delete the memory of the Paris trip.", request="delete").json()["result"]
        assert deleted["kind"] == "unsupported"
        assert deleted["detail"] == "MEMORY_MUTATION_UNSUPPORTED"
        assert counts(file) == (0, 0, 0, 0)

        nothing = voice(client, chat, "Restore excluded memories.", request="restore").json()["result"]
        assert nothing["kind"] == "unchanged"
        assert nothing["detail"] == "NO_EXCLUDED_MEMORIES"
        assert counts(file) == (0, 0, 0, 0)


@pytest.mark.usefixtures("legacy_forget")
def test_a_new_memory_request_disarms_the_previous_proposal(tmp_path):
    file, settings = configured(tmp_path, "disarm")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(
            file,
            chat,
            [("name-a", "My name is A"), ("name-b", "My name is B"), ("city", "My city is Tripoli")],
        )

        armed = voice(client, chat, "Forget my city.", request="p-city").json()["result"]
        assert armed["detail"] == "MEMORY_EXCLUSION_PROPOSED"

        # An ambiguous follow-up retires the old question; a later "yes" cannot
        # apply the proposal the user has already moved past.
        ambiguous = voice(client, chat, "Forget my name.", request="p-name").json()["result"]
        assert ambiguous["detail"] == "MEMORY_TARGET_AMBIGUOUS"
        assert voice(client, chat, "Yes.", request="late-yes").json() == {"handled": False}
        assert counts(file) == (0, 0, 0, 1)

        voice(client, chat, "Forget my city.", request="p-city-2")
        global_result = voice(client, chat, "Forget my city everywhere.", request="global").json()["result"]
        assert global_result["detail"] == "MEMORY_GLOBAL_RETRACTION_UNAVAILABLE"
        assert voice(client, chat, "Yes.", request="late-yes-2").json() == {"handled": False}
        assert counts(file) == (0, 0, 0, 2)


@pytest.mark.usefixtures("legacy_forget")
def test_restore_targets_only_the_latest_conversation_scoped_exclusion(tmp_path):
    file, settings = configured(tmp_path, "latest")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("name", "My name is Khengari"), ("city", "My city is Tripoli")])

        voice(client, chat, "Forget my name.", request="p-name")
        voice(client, chat, "Yes.", request="c-name")
        assert counts(file)[:3] == (1, 1, 1)

        voice(client, chat, "Forget my city.", request="p-city", revision=1)
        voice(client, chat, "Yes.", request="c-city", revision=1)
        assert counts(file)[:3] == (2, 2, 2)
        assert client.get(f"/v1/conversations/{chat}/context").json()["excluded_ids"] == ["city", "name"]

        question = voice(client, chat, "Restore excluded memories.", request="p-restore", revision=2)
        spoken = question.json()["result"]["spoken"]
        assert spoken == "Restore 'My city is Tripoli' to this conversation? Say yes to restore, or no."

        restored = voice(client, chat, "Yes.", request="c-restore", revision=2).json()["result"]
        assert restored["detail"] == "MEMORY_RESTORED"
        assert counts(file)[:3] == (3, 2, 1)
        assert client.get(f"/v1/conversations/{chat}/context").json()["excluded_ids"] == ["name"]


@pytest.mark.usefixtures("legacy_forget")
def test_another_owner_cannot_use_the_proposal_token(tmp_path):
    file, settings = configured(tmp_path, "cross-owner")
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("name", "My name is Khengari")])
        voice(client, chat, "Forget my name.", request="proposal-1")
        pending, _ = latest_proposal(file)
        token = pending["token"]

    with TestClient(create_app(settings)) as other:
        other.post("/v1/demo-session")
        other_chat = other.post("/v1/conversations").json()["id"]
        response = other.post(
            f"/v1/conversations/{chat}/context/confirm",
            json={"proposal": token, "expected_revision": 0, "idempotency_key": "forged"},
        )
        assert response.status_code == 404
        assert counts(file) == (0, 0, 0, 1)
        assert other_chat


@pytest.mark.usefixtures("legacy_forget")
def test_an_unrelated_turn_disarms_the_pending_proposal_so_a_later_yes_changes_nothing(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        chat = new_chat(client)
        seed(file, chat, [("name", "My name is Khengari")])
        assert (
            voice(client, chat, "Forget my name.", request="p1").json()["result"]["detail"]
            == "MEMORY_EXCLUSION_PROPOSED"
        )
        assert voice(client, chat, "What is the weather like today?", request="u1").json() == {"handled": False}
        stray = voice(client, chat, "Yes.", request="y1")
        assert stray.json() == {"handled": False}, "a yes to another question must not apply the old proposal"
        assert counts(file) == (0, 0, 0, 1)
        _, outcome = latest_proposal(file)
        assert outcome == "superseded"


def test_undo_last_hide_from_one_conversation_never_restores_or_quotes_another_chats_temporary_memory(tmp_path):
    file, settings = configured(tmp_path, "paired-undo")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat_a = client.post("/v1/conversations").json()["id"]
        chat_b = client.post("/v1/conversations").json()["id"]
        seed(file, chat_a, [("global-fact", "My favourite colour is blue")], scope="global", status="hidden")
        seed(file, chat_a, [("temp-a", "A-only secret")], scope="conversation", status="hidden")
        seed(file, chat_b, [("temp-b", "B-only note")], scope="conversation", status="hidden")
        # A's temporary memory was hidden most recently; B must step over it.
        seed_operation(
            file,
            operation_id="hide-temp-a",
            owner_id=owner,
            conversation_id=chat_a,
            command_type=HIDE_OPERATION,
            payload={"memory_id": "temp-a"},
            created_at="2026-11-12T00:00:03+00:00",
        )
        seed_operation(
            file,
            operation_id="hide-temp-b",
            owner_id=owner,
            conversation_id=chat_b,
            command_type=HIDE_OPERATION,
            payload={"memory_id": "temp-b"},
            created_at="2026-11-12T00:00:02+00:00",
        )
        seed_operation(
            file,
            operation_id="hide-global",
            owner_id=owner,
            conversation_id=chat_a,
            command_type=HIDE_OPERATION,
            payload={"memory_id": "global-fact"},
            created_at="2026-11-12T00:00:01+00:00",
        )

        first = voice(client, chat_b, "Undo", request="undo-b-1").json()["result"]
        assert first["detail"] == "MEMORY_UNHIDDEN"
        assert "B-only note" in first["spoken"]
        assert "A-only secret" not in first["spoken"]
        assert memory_status(file, "temp-b") == "active"
        assert memory_status(file, "temp-a") == "hidden"
        assert memory_status(file, "global-fact") == "hidden"

        second = voice(client, chat_b, "Undo", request="undo-b-2").json()["result"]
        assert second["detail"] == "MEMORY_UNHIDDEN"
        assert "My favourite colour is blue" in second["spoken"]
        assert memory_status(file, "global-fact") == "active"
        assert memory_status(file, "temp-a") == "hidden"

        third = voice(client, chat_b, "Undo", request="undo-b-3").json()["result"]
        assert third["detail"] == "NO_HIDDEN_MEMORY"
        assert "A-only secret" not in json.dumps(third)
        assert memory_status(file, "temp-a") == "hidden"


def test_restore_from_one_conversation_never_speaks_or_restores_another_chats_temporary_memory(tmp_path):
    file, settings = configured(tmp_path, "paired-restore")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat_a = client.post("/v1/conversations").json()["id"]
        chat_b = client.post("/v1/conversations").json()["id"]
        seed(file, chat_a, [("temp-a", "A-only secret")], scope="conversation")
        seed(file, chat_b, [("temp-b", "B-only note")], scope="conversation")
        # The most recent B exclusion points at A's temporary memory; the real B one is older.
        seed_operation(
            file,
            operation_id="op-exclude-a",
            owner_id=owner,
            conversation_id=chat_b,
            command_type="exclude",
            created_at="2026-11-12T00:00:02+00:00",
            revision_after=0,
        )
        seed_operation(
            file,
            operation_id="op-exclude-b",
            owner_id=owner,
            conversation_id=chat_b,
            command_type="exclude",
            created_at="2026-11-12T00:00:01+00:00",
            revision_after=0,
        )
        seed_exclusion(
            file,
            exclusion_id="ex-temp-a",
            owner_id=owner,
            conversation_id=chat_b,
            memory_id="temp-a",
            operation_id="op-exclude-a",
        )
        seed_exclusion(
            file,
            exclusion_id="ex-temp-b",
            owner_id=owner,
            conversation_id=chat_b,
            memory_id="temp-b",
            operation_id="op-exclude-b",
        )

        question = voice(client, chat_b, "Restore excluded memories.", request="restore-b").json()["result"]
        assert question["detail"] == "MEMORY_RESTORE_PROPOSED"
        assert "B-only note" in question["spoken"]
        assert "A-only secret" not in question["spoken"]
        assert "0 memories" not in question["spoken"]

        restored = voice(client, chat_b, "Yes.", request="restore-b-confirm").json()["result"]
        assert restored["detail"] == "MEMORY_RESTORED"
        assert exclusion_active(file, "op-exclude-a") is True
        assert exclusion_active(file, "op-exclude-b") is False
        assert memory_status(file, "temp-a") == "active"
