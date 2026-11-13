"""Voice admission idempotency: replay, conflict, ownership and terminal outcomes."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from test_session_lifecycle import (
    FakeLiveKitAPI,
    build,
    conversation,
    ledger,
    reserve_ledger,
    session_row,
    sessions_with_status,
)

KEY = "retry-key-1"


def start_keyed(client, conversation_id, key=KEY, **overrides):
    body = {"conversation_id": conversation_id, "voice_id": "test-voice", "request_key": key, **overrides}
    return client.post("/v1/voice/sessions", json=body)


def admissions(db_path):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM voice_admissions ORDER BY created_at, id")]


def sessions_for(db_path, owner_id):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM voice_sessions WHERE owner_id=?", (owner_id,))]


def test_lost_response_retry_replays_session_without_second_dispatch_or_quota(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat = conversation(alice)
        reserve_ledger(alice)
        before = ledger(db_path)
        first = start_keyed(alice, chat)
        assert first.status_code == 201, first.text
        session_id = first.json()["id"]
        room = session_row(db_path, session_id)["room_name"]
        assert state["dispatched"] == [room]

        retried = start_keyed(alice, chat)
        assert retried.status_code == 201, retried.text
        assert retried.json()["id"] == session_id
        assert retried.json()["url"] == first.json()["url"]
        assert retried.json()["expires_at"] == first.json()["expires_at"]
        assert retried.json()["token"]
        assert state["dispatched"] == [room]
        assert len(sessions_with_status(db_path, "starting")) == 1
        assert len(admissions(db_path)) == 1
        assert admissions(db_path)[0]["state"] == "dispatched"
        assert admissions(db_path)[0]["session_id"] == session_id
        assert ledger(db_path) == before


def test_changed_arguments_under_the_same_key_conflict(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        chat = conversation(alice)
        assert start_keyed(alice, chat).status_code == 201
        for changed in ({"language": "ar"}, {"response_tier": 5}, {"response_preference": "brief"}):
            response = start_keyed(alice, chat, **changed)
            assert response.status_code == 409, changed
            assert response.json()["detail"]["code"] == "VOICE_ADMISSION_CONFLICT"
        second_chat = conversation(alice)
        changed_conversation = start_keyed(alice, second_chat)
        assert changed_conversation.status_code == 409
        assert len(state["dispatched"]) == 1
        assert len(admissions(db_path)) == 1


def test_cross_owner_key_reuse_cannot_expose_another_session(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        alice_chat = conversation(alice)
        first = start_keyed(alice, alice_chat)
        assert first.status_code == 201
        bob = TestClient(app)
        bob_chat = conversation(bob)
        second = start_keyed(bob, bob_chat)
        assert second.status_code == 201, second.text
        assert second.json()["id"] != first.json()["id"]
        assert len(state["dispatched"]) == 2
        assert len(admissions(db_path)) == 2
        assert {row["owner_id"] for row in admissions(db_path)} == {
            session_row(db_path, first.json()["id"])["owner_id"],
            session_row(db_path, second.json()["id"])["owner_id"],
        }
        assert bob.post(f"/v1/voice/sessions/{first.json()['id']}/end").status_code == 404


def test_failed_dispatch_replay_cannot_replace_or_refund(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat = conversation(alice)
        reserve_ledger(alice)
        before = ledger(db_path)
        state["dispatch_fails"] = True
        first = start_keyed(alice, chat)
        assert first.status_code == 503
        assert first.json()["detail"]["code"] == "VOICE_DISPATCH_FAILED"
        assert admissions(db_path)[0]["state"] == "failed"
        assert len(sessions_with_status(db_path, "failed")) == 1

        state["dispatch_fails"] = False
        retried = start_keyed(alice, chat)
        assert retried.status_code == 503
        assert retried.json()["detail"]["code"] == "VOICE_DISPATCH_FAILED"
        assert len(state["dispatched"]) == 1
        assert len(sessions_for(db_path, admissions(db_path)[0]["owner_id"])) == 1
        assert ledger(db_path) == before


def test_uncertain_dispatch_replay_is_typed_and_not_replaced(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat = conversation(alice)
        reserve_ledger(alice)
        before = ledger(db_path)
        dispatched = []

        async def uncertain(self, request):
            dispatched.append(request.room)
            raise TimeoutError("lost dispatch response")

        monkeypatch.setattr(FakeLiveKitAPI, "dispatch", uncertain)
        first = start_keyed(alice, chat)
        assert first.status_code == 503
        assert admissions(db_path)[0]["state"] == "uncertain"
        assert admissions(db_path)[0]["error_code"] == "VOICE_DISPATCH_UNCERTAIN"

        retried = start_keyed(alice, chat)
        assert retried.status_code == 503
        assert retried.json()["detail"]["code"] == "VOICE_DISPATCH_UNCERTAIN"
        assert len(dispatched) == 1
        assert len(sessions_for(db_path, admissions(db_path)[0]["owner_id"])) == 1
        assert ledger(db_path) == before


def test_ended_and_expired_outcomes_never_dispatch_a_replacement(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat = conversation(alice)
        created = start_keyed(alice, chat)
        session_id = created.json()["id"]
        assert alice.post(f"/v1/voice/sessions/{session_id}/end").status_code == 200
        ended = start_keyed(alice, chat)
        assert ended.status_code == 409
        assert ended.json()["detail"]["code"] == "VOICE_ADMISSION_ENDED"
        assert len(state["dispatched"]) == 1
        assert len(sessions_for(db_path, admissions(db_path)[0]["owner_id"])) == 1

        second = start_keyed(alice, chat, key="retry-key-2")
        assert second.status_code == 201
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute(
                "UPDATE voice_sessions SET expires_at=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), second.json()["id"]),
            )
            connection.execute("UPDATE voice_admissions SET state='dispatched' WHERE request_key='retry-key-2'")
        expired = start_keyed(alice, chat, key="retry-key-2")
        assert expired.status_code == 410
        assert expired.json()["detail"]["code"] == "VOICE_ADMISSION_EXPIRED"
        assert len(state["dispatched"]) == 2


def test_stale_in_progress_claim_becomes_uncertain_without_dispatch(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat = conversation(alice)
        first = start_keyed(alice, chat)
        assert first.status_code == 201
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute(
                "UPDATE voice_admissions SET state='in_progress', session_id=NULL, created_at=?",
                ((datetime.now(UTC) - timedelta(minutes=5)).isoformat(),),
            )
        retried = start_keyed(alice, chat)
        assert retried.status_code == 503
        assert retried.json()["detail"]["code"] == "VOICE_ADMISSION_UNCERTAIN"
        assert len(state["dispatched"]) == 1
        assert admissions(db_path)[0]["state"] == "uncertain"


def test_quota_rejection_is_a_terminal_outcome_for_the_key(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    app.state.settings.demo_calls_per_identity = 1
    with alice:
        chat = conversation(alice)
        first = start_keyed(alice, chat, key="first")
        assert first.status_code == 201
        assert alice.post(f"/v1/voice/sessions/{first.json()['id']}/end").status_code == 200
        rejected = start_keyed(alice, chat, key="second")
        assert rejected.status_code == 429
        assert admissions(db_path)[-1]["state"] == "failed"
        assert admissions(db_path)[-1]["error_status"] == 429
        retried = start_keyed(alice, chat, key="second")
        assert retried.status_code == 429
        assert retried.json()["detail"]["code"] == "DEMO_BUDGET_REACHED"
        assert len(state["dispatched"]) == 1


def test_legacy_callers_without_request_key_keep_creating_sessions(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat = conversation(alice)
        first = alice.post("/v1/voice/sessions", json={"conversation_id": chat, "voice_id": "test-voice"})
        assert first.status_code == 201
        assert alice.post(f"/v1/voice/sessions/{first.json()['id']}/end").status_code == 200
        second = alice.post("/v1/voice/sessions", json={"conversation_id": chat, "voice_id": "test-voice"})
        assert second.status_code == 201, second.text
        assert first.json()["id"] != second.json()["id"]
        assert len(state["dispatched"]) == 2
        assert admissions(db_path) == []
