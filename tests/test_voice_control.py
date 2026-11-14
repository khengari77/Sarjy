"""Authorized live voice control: catalog, session binding, ACK fencing."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.domain.voice_control import verify_voice_control
from app.main import create_app
from app.services.voices import VoiceCatalogError, VoiceOption
from fastapi.testclient import TestClient

SECRET = "test-worker-secret"


class Catalog:
    async def require(self, voice_id):
        if voice_id not in {"voice-a", "voice-b", "voice-c"}:
            raise VoiceCatalogError("VOICE_UNAVAILABLE", 422)
        return VoiceOption(id=voice_id, name=voice_id.upper())


class Delivery:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def send(self, *, room_name, token):
        if self.fail:
            from app.services.voice_control import VoiceControlError

            raise VoiceControlError("VOICE_CONTROL_UNAVAILABLE")
        self.calls.append((room_name, token))


def setup(tmp_path, *, fail_delivery=False):
    path = tmp_path / "voice-control.db"
    delivery = Delivery(fail=fail_delivery)
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{path}", agent_service_secret=SECRET, _env_file=None),
        voice_catalog=Catalog(),
        voice_control_delivery=delivery,
    )
    return app, path, delivery


def seed_session(path, *, conversation, owner, voice="voice-a", revision=0):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline,"
            "active_voice_id,active_settings_revision) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "session",
                conversation,
                owner,
                "room",
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                json.dumps({"tts": {"voice_id": voice}}),
                voice,
                revision,
            ),
        )


def headers(room="room", session="session"):
    return {"x-agent-secret": SECRET, "x-session-id": session, "x-room-name": room}


def chat(client):
    owner = client.post("/v1/demo-session").json()["id"]
    conversation = client.post("/v1/conversations").json()["id"]
    return owner, conversation


def submit(client, conversation, *, request, revision, voice):
    return client.post(
        "/v1/actions",
        json={
            "conversation_id": conversation,
            "request_id": request,
            "expected_revision": revision,
            "source": "ui",
            "action": {"kind": "SelectApprovedVoice", "voice_id": voice},
        },
    )


def ack(client, *, request, voice, revision, status="applied", error=None, **header_overrides):
    response = client.post(
        "/internal/agent/voice/ack",
        headers=headers(**header_overrides),
        json={
            "request_id": request,
            "voice_id": voice,
            "settings_revision": revision,
            "status": status,
            "error_code": error,
        },
    )
    return response


def test_approved_change_reaches_exact_room_and_ack_confirms_active_voice(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        bootstrap = client.post("/internal/agent/bootstrap", headers=headers())
        assert bootstrap.status_code == 200
        assert bootstrap.json()["active_voice_id"] == "voice-a"
        assert bootstrap.json()["settings_revision"] == 0
        response = submit(client, conversation, request="r1", revision=0, voice="voice-b")
        assert response.status_code == 200
        control = response.json()["result"]["voice_control"]
        assert control["status"] == "dispatched"
        assert control["active_voice_id"] == "voice-a"
        assert control["voice_id"] == "voice-b"

        assert len(delivery.calls) == 1
        room, token = delivery.calls[0]
        assert room == "room"
        payload = verify_voice_control(token, SECRET)
        assert payload.pop("expires_at") > datetime.now(UTC).isoformat()
        assert payload == {
            "audience": "sarjy.voice.change",
            "session_id": "session",
            "room_name": "room",
            "conversation_id": conversation,
            "owner_id": owner,
            "voice_id": "voice-b",
            "request_id": "r1",
            "settings_revision": 1,
        }

        before = client.get("/v1/voice/sessions/session/voice").json()
        assert before["status"] == "dispatched"
        assert before["active_voice_id"] == "voice-a"

        confirmed = ack(client, request="r1", voice="voice-b", revision=1)
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "applied"
        assert confirmed.json()["active_voice_id"] == "voice-b"
        assert confirmed.json()["active_settings_revision"] == 1
        after = client.get("/v1/voice/sessions/session/voice").json()
        assert after == confirmed.json()

        with closing(sqlite3.connect(path)) as db, db:
            assert db.execute("SELECT COUNT(*) FROM voice_sessions").fetchone()[0] == 1
            assert db.execute("SELECT room_name FROM voice_sessions").fetchone()[0] == "room"


def test_unapproved_voice_is_rejected_before_persistence_or_delivery(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        assert submit(client, conversation, request="r1", revision=0, voice="arbitrary").status_code == 422
        assert client.get("/v1/settings").json()["revision"] == 0
        assert delivery.calls == []
        with closing(sqlite3.connect(path)) as db, db:
            assert db.execute("SELECT COUNT(*) FROM voice_controls").fetchone()[0] == 0


def test_duplicate_submission_and_duplicate_ack_apply_once(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        first = submit(client, conversation, request="r1", revision=0, voice="voice-b")
        retry = submit(client, conversation, request="r1", revision=0, voice="voice-b")
        assert first.status_code == retry.status_code == 200
        assert first.json() == retry.json()
        assert len(delivery.calls) == 1

        applied = ack(client, request="r1", voice="voice-b", revision=1)
        replay = ack(client, request="r1", voice="voice-b", revision=1)
        assert replay.json() == applied.json()
        with closing(sqlite3.connect(path)) as db, db:
            assert db.execute("SELECT COUNT(*) FROM voice_controls").fetchone()[0] == 1
            assert db.execute("SELECT active_voice_id FROM voice_sessions").fetchone()[0] == "voice-b"


def test_concurrent_change_supersedes_older_and_stale_ack_is_ignored(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        submit(client, conversation, request="r1", revision=0, voice="voice-b")
        submit(client, conversation, request="r2", revision=1, voice="voice-c")

        stale = ack(client, request="r1", voice="voice-b", revision=1)
        assert stale.status_code == 200
        assert stale.json()["status"] == "superseded"
        assert stale.json()["active_voice_id"] == "voice-a"

        latest = ack(client, request="r2", voice="voice-c", revision=2)
        assert latest.json()["status"] == "applied"
        assert latest.json()["active_voice_id"] == "voice-c"

        # A late ACK from the superseded request cannot roll the active voice back.
        late = ack(client, request="r1", voice="voice-b", revision=1)
        assert late.json()["active_voice_id"] == "voice-c"
        assert late.json()["active_settings_revision"] == 2


def test_worker_failure_keeps_previous_active_voice_and_reports_error(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        submit(client, conversation, request="r1", revision=0, voice="voice-b")
        failed = ack(client, request="r1", voice="voice-b", revision=1, status="failed", error="VOICE_SWITCH_FAILED")
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert failed.json()["active_voice_id"] == "voice-a"
        assert failed.json()["active_settings_revision"] == 0
        assert failed.json()["error_code"] == "VOICE_SWITCH_FAILED"
        # Future preference persists independently from the confirmed active voice.
        assert client.get("/v1/settings").json()["approved_voice_id"] == "voice-b"


def test_delivery_failure_reports_actionable_error_without_claiming_success(tmp_path):
    app, path, delivery = setup(tmp_path, fail_delivery=True)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        control = submit(client, conversation, request="r1", revision=0, voice="voice-b").json()["result"][
            "voice_control"
        ]
        assert control["status"] == "failed"
        assert control["error_code"] == "VOICE_CONTROL_UNAVAILABLE"
        assert control["active_voice_id"] == "voice-a"
        assert delivery.calls == []
        assert client.get("/v1/voice/sessions/session/voice").json()["active_voice_id"] == "voice-a"


def test_ack_requires_worker_auth_exact_session_and_matching_control(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        submit(client, conversation, request="r1", revision=0, voice="voice-b")

        anonymous = client.post(
            "/internal/agent/voice/ack",
            json={"request_id": "r1", "voice_id": "voice-b", "settings_revision": 1, "status": "applied"},
        )
        assert anonymous.status_code == 401
        assert ack(client, request="r1", voice="voice-b", revision=1, room="other-room").status_code == 403
        assert ack(client, request="r1", voice="voice-b", revision=1, session="other").status_code == 403
        assert ack(client, request="missing", voice="voice-b", revision=1).status_code == 404
        mismatch = ack(client, request="r1", voice="voice-c", revision=1)
        assert mismatch.status_code == 409
        assert mismatch.json()["detail"]["code"] == "VOICE_CONTROL_MISMATCH"


def test_change_without_an_active_session_is_a_next_call_preference_only(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        response = submit(client, conversation, request="r1", revision=0, voice="voice-b")
        assert response.status_code == 200
        assert "voice_control" not in response.json()["result"]
        assert delivery.calls == []
        assert client.get("/v1/settings").json()["approved_voice_id"] == "voice-b"
        with closing(sqlite3.connect(path)) as db, db:
            assert db.execute("SELECT COUNT(*) FROM voice_controls").fetchone()[0] == 0


def test_voice_state_is_owner_scoped(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as alice:
        owner, conversation = chat(alice)
        seed_session(path, conversation=conversation, owner=owner)
        with closing(TestClient(app)) as bob:
            bob.post("/v1/demo-session")
            assert bob.get("/v1/voice/sessions/session/voice").status_code == 404


def test_stuck_dispatched_control_expires_and_rejects_the_late_ack(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        submit(client, conversation, request="r1", revision=0, voice="voice-b")
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "UPDATE voice_controls SET expires_at=? WHERE request_id='r1'",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
            )
        expired = client.get("/v1/voice/sessions/session/voice").json()
        assert expired["status"] == "failed"
        assert expired["error_code"] == "VOICE_SWITCH_EXPIRED"
        assert expired["active_voice_id"] == "voice-a"

        late = ack(client, request="r1", voice="voice-b", revision=1)
        assert late.status_code == 200
        assert late.json()["status"] == "failed"
        assert late.json()["active_voice_id"] == "voice-a"
        assert late.json()["active_settings_revision"] == 0


def test_ending_the_session_settles_in_flight_controls(tmp_path):
    app, path, delivery = setup(tmp_path)
    with TestClient(app) as client:
        owner, conversation = chat(client)
        seed_session(path, conversation=conversation, owner=owner)
        submit(client, conversation, request="r1", revision=0, voice="voice-b")
        # Room cleanup is unconfigured in tests; the control settlement commits first.
        assert client.post("/v1/voice/sessions/session/end").status_code == 503
        state = client.get("/v1/voice/sessions/session/voice").json()
        assert state["status"] == "failed"
        assert state["error_code"] == "VOICE_SESSION_ENDED"
        assert state["active_voice_id"] == "voice-a"
