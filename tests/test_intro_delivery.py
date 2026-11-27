import asyncio
import base64
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.domain.intro import INTRO, SHORT_GREETING
from app.main import create_app
from app.services.intro import mark_intro_seen
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_voices import configured, configured_with_default, livekit_control_plane  # noqa: F401

pytestmark = pytest.mark.usefixtures("livekit_control_plane")


def _start(client, conversation_id, *, voice_id="aura-2-apollo-en", intro_delivery=None):
    body = {"conversation_id": conversation_id}
    if voice_id is not None:
        body["voice_id"] = voice_id
    if intro_delivery is not None:
        body["intro_delivery"] = intro_delivery
    return client.post("/v1/voice/sessions", json=body)


def _worker_headers(tmp_path, session_id):
    with closing(sqlite3.connect(tmp_path / "voice.db")) as db:
        room = db.execute("SELECT room_name FROM voice_sessions WHERE id=?", (session_id,)).fetchone()[0]
    return {"x-agent-secret": "worker-secret", "x-session-id": session_id, "x-room-name": room}


def _stored_pipeline(tmp_path, session_id):
    with closing(sqlite3.connect(tmp_path / "voice.db")) as db:
        return json.loads(db.execute("SELECT pipeline FROM voice_sessions WHERE id=?", (session_id,)).fetchone()[0])


def _intro_seen(tmp_path, owner_id):
    with closing(sqlite3.connect(tmp_path / "voice.db")) as db:
        row = db.execute("SELECT intro_seen_at FROM user_settings WHERE owner_id=?", (owner_id,)).fetchone()
    return row[0] if row else None


def _mark_intro_in_new_session(db_path, owner_id):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 30})
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                await mark_intro_seen(db, owner_id)
        finally:
            await engine.dispose()

    return run()


def _expire_session(tmp_path, session_id):
    with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
        db.execute(
            "UPDATE voice_sessions SET expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), session_id),
        )


def test_worker_delivery_is_the_default_and_bootstrap_still_speaks(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        response = _start(client, chat)
        assert response.status_code == 201, response.text
        assert "intro" not in response.json()
        assert "intro_delivery" not in _stored_pipeline(tmp_path, response.json()["id"])
        bootstrap = client.post("/internal/agent/bootstrap", headers=_worker_headers(tmp_path, response.json()["id"]))
        assert bootstrap.status_code == 200, bootstrap.text
        assert bootstrap.json()["intro"] == {"kind": "full", "text": INTRO["en"]}


def test_browser_delivery_returns_the_full_intro_and_keeps_the_worker_silent(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        response = _start(client, chat, intro_delivery="browser")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["intro"] == {
            "kind": "full",
            "text": INTRO["en"],
            "asset": "aura-2-apollo-en/full.mp3",
        }
        pipeline = _stored_pipeline(tmp_path, body["id"])
        assert (pipeline["intro_delivery"], pipeline["intro_kind"]) == ("browser", "full")
        bootstrap = client.post("/internal/agent/bootstrap", headers=_worker_headers(tmp_path, body["id"]))
        assert bootstrap.status_code == 200, bootstrap.text
        assert bootstrap.json()["intro"] is None


def test_browser_delivery_asset_uses_the_saved_voice_fallback(tmp_path):
    settings = configured_with_default(tmp_path, approved_voice_ids="aura-2-thalia-en")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        # The saved id was approved when stored but later removed from the allow-list.
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            db.execute(
                "INSERT INTO user_settings"
                " (owner_id, speaking_speed, captions_visible, approved_voice_id, response_preference, revision, updated_at)"
                " VALUES (?, 1.0, 1, 'aura-2-apollo-en', 'automatic', 1, '2026-01-01T00:00:00+00:00')",
                (owner,),
            )
        response = _start(client, chat, voice_id=None, intro_delivery="browser")
        assert response.status_code == 201, response.text
        body = response.json()
        assert (body["voice"]["id"], body["voice_source"]) == ("aura-2-thalia-en", "default")
        assert body["intro"]["asset"] == "aura-2-thalia-en/full.mp3"


def test_mark_intro_seen_is_race_safe_without_a_settings_row(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db:
            assert db.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0] == 0

    async def race():
        await asyncio.gather(
            _mark_intro_in_new_session(tmp_path / "voice.db", owner),
            _mark_intro_in_new_session(tmp_path / "voice.db", owner),
        )

    asyncio.run(race())
    marked = _intro_seen(tmp_path, owner)
    assert marked is not None
    with closing(sqlite3.connect(tmp_path / "voice.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0] == 1
    asyncio.run(_mark_intro_in_new_session(tmp_path / "voice.db", owner))
    assert _intro_seen(tmp_path, owner) == marked


def test_unsafe_resolved_voice_id_has_no_asset():
    from app.routes.voice import StartVoice, _plan_intro

    body = StartVoice(conversation_id="chat", intro_delivery="browser")
    assert _plan_intro(body, seen=False, tts_provider="deepgram_direct", voice_id="../x") == (
        {},
        {"intro": None},
    )


def test_dispatch_and_token_carry_no_intro_flags(tmp_path, monkeypatch):
    from livekit import api

    captured = {}

    class ControlPlane:
        def __init__(self, *args, **kwargs):
            self.agent_dispatch = SimpleNamespace(create_dispatch=self.dispatch)
            self.room = SimpleNamespace(delete_room=self.delete)

        async def dispatch(self, request):
            captured["dispatch"] = request

        async def delete(self, request):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(api, "LiveKitAPI", ControlPlane)
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        body = _start(client, chat, intro_delivery="browser").json()
        assert json.loads(captured["dispatch"].metadata) == {"session_id": body["id"]}
        payload = json.loads(base64.urlsafe_b64decode(body["token"].split(".")[1] + "=="))
        assert "intro_delivery" not in payload and "intro_kind" not in payload


def test_session_flags_do_not_change_the_recorded_provider_config(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]

        worker = _start(client, chat).json()
        worker_headers = _worker_headers(tmp_path, worker["id"])
        assert client.post("/internal/agent/bootstrap", headers=worker_headers).status_code == 200
        worker_context = client.post(
            "/internal/agent/turn/start", headers=worker_headers, json={"request_id": "worker", "text": "Hello"}
        ).json()["context"]
        client.post(f"/v1/voice/sessions/{worker['id']}/end")

        browser = _start(client, chat, intro_delivery="browser").json()
        browser_headers = _worker_headers(tmp_path, browser["id"])
        assert client.post("/internal/agent/bootstrap", headers=browser_headers).status_code == 200
        browser_context = client.post(
            "/internal/agent/turn/start", headers=browser_headers, json={"request_id": "browser", "text": "Hello"}
        ).json()["context"]

        assert browser_context["pipeline_config"] == worker_context["pipeline_config"]
        assert not {"intro_delivery", "intro_kind", "bootstrapped"} & browser_context["pipeline_config"].keys()
        assert (
            browser_context["manifest"]["pipeline_config_sha256"]
            == worker_context["manifest"]["pipeline_config_sha256"]
        )


def test_arabic_on_aura_has_no_browser_greeting():
    from app.routes.voice import StartVoice, _plan_intro

    body = StartVoice(conversation_id="chat", language="ar", intro_delivery="browser")
    # Nothing is stored as browser delivery, so bootstrap keeps the worker's (also empty) intro path.
    assert _plan_intro(body, seen=False, tts_provider="deepgram_direct", voice_id="voice") == (
        {},
        {"intro": None},
    )


def test_invalid_intro_delivery_is_rejected_like_other_fields(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        response = _start(client, chat, intro_delivery="server")
        assert response.status_code == 422
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            assert db.execute("SELECT COUNT(*) FROM voice_sessions").fetchone()[0] == 0


def test_browser_delivery_without_a_speakable_greeting_falls_back_to_the_worker(tmp_path, monkeypatch):
    import app.routes.voice as voice_routes

    monkeypatch.setattr(voice_routes, "intro_for", lambda *args, **kwargs: None)
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        response = _start(client, chat, intro_delivery="browser")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["intro"] is None
        assert "intro_delivery" not in _stored_pipeline(tmp_path, body["id"])
        bootstrap = client.post("/internal/agent/bootstrap", headers=_worker_headers(tmp_path, body["id"]))
        assert bootstrap.json()["intro"] == {"kind": "full", "text": INTRO["en"]}


def test_intro_complete_marks_only_after_bootstrap_for_a_live_full_intro(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        session = _start(client, chat, intro_delivery="browser").json()
        # A call that never reaches the worker must replay the introduction.
        assert client.post(f"/v1/voice/sessions/{session['id']}/intro-complete").status_code == 204
        assert _intro_seen(tmp_path, owner) is None
        headers = _worker_headers(tmp_path, session["id"])
        assert client.post("/internal/agent/bootstrap", headers=headers).status_code == 200
        assert client.post(f"/v1/voice/sessions/{session['id']}/intro-complete").status_code == 204
        marked = _intro_seen(tmp_path, owner)
        assert marked is not None
        # Idempotent: the second confirmation leaves the same timestamp.
        assert client.post(f"/v1/voice/sessions/{session['id']}/intro-complete").status_code == 204
        assert _intro_seen(tmp_path, owner) == marked


def test_intro_complete_is_a_noop_for_worker_delivery(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        session = _start(client, chat).json()
        client.post("/internal/agent/bootstrap", headers=_worker_headers(tmp_path, session["id"]))
        assert client.post(f"/v1/voice/sessions/{session['id']}/intro-complete").status_code == 204
        assert _intro_seen(tmp_path, owner) is None


def test_intro_complete_does_not_mark_an_expired_session(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        session = _start(client, chat, intro_delivery="browser").json()
        client.post("/internal/agent/bootstrap", headers=_worker_headers(tmp_path, session["id"]))
        _expire_session(tmp_path, session["id"])
        assert client.post(f"/v1/voice/sessions/{session['id']}/intro-complete").status_code == 204
        assert _intro_seen(tmp_path, owner) is None


def test_second_bootstrap_is_idempotent_and_keeps_session_flags(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        session = _start(client, chat, intro_delivery="browser").json()
        headers = _worker_headers(tmp_path, session["id"])
        first = client.post("/internal/agent/bootstrap", headers=headers)
        second = client.post("/internal/agent/bootstrap", headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.json()["intro"] is second.json()["intro"] is None
        assert "intro_delivery" not in second.json()["pipeline"]
        stored = _stored_pipeline(tmp_path, session["id"])
        assert (stored["intro_delivery"], stored["intro_kind"], stored["bootstrapped"]) == (
            "browser",
            "full",
            True,
        )


def test_intro_complete_never_marks_a_short_greeting(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        first = _start(client, chat, intro_delivery="browser").json()
        headers = _worker_headers(tmp_path, first["id"])
        client.post("/internal/agent/bootstrap", headers=headers)
        client.post(f"/v1/voice/sessions/{first['id']}/intro-complete")
        assert _intro_seen(tmp_path, owner) is not None
        client.post(f"/v1/voice/sessions/{first['id']}/end")
        second = _start(client, chat, intro_delivery="browser").json()
        assert second["intro"]["kind"] == "short"
        assert second["intro"]["text"] == SHORT_GREETING["en"]
        # Clear the marker so the kind guard, not the existing timestamp, decides.
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            db.execute("UPDATE user_settings SET intro_seen_at=NULL WHERE owner_id=?", (owner,))
        client.post("/internal/agent/bootstrap", headers=_worker_headers(tmp_path, second["id"]))
        assert client.post(f"/v1/voice/sessions/{second['id']}/intro-complete").status_code == 204
        assert _intro_seen(tmp_path, owner) is None


def test_intro_complete_does_not_mark_an_ended_session(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        session = _start(client, chat, intro_delivery="browser").json()
        client.post("/internal/agent/bootstrap", headers=_worker_headers(tmp_path, session["id"]))
        assert client.post(f"/v1/voice/sessions/{session['id']}/end").status_code == 200
        assert client.post(f"/v1/voice/sessions/{session['id']}/intro-complete").status_code == 204
        assert _intro_seen(tmp_path, owner) is None


def test_intro_complete_rejects_another_owner_and_unknown_sessions(tmp_path):
    app = create_app(configured(tmp_path))
    with TestClient(app) as alice:
        alice.post("/v1/demo-session")
        chat = alice.post("/v1/conversations").json()["id"]
        session = _start(alice, chat, intro_delivery="browser").json()
        with TestClient(app) as bob:
            bob.post("/v1/demo-session")
            assert bob.post(f"/v1/voice/sessions/{session['id']}/intro-complete").status_code == 404
            assert bob.post("/v1/voice/sessions/unknown/intro-complete").status_code == 404
