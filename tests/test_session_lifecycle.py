import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.config import Settings
from app.main import create_app
from app.services.voices import VoiceOption
from fastapi.testclient import TestClient
from livekit import api as livekit_api

AGENT_SECRET = "test-agent-secret"


class FakeCatalog:
    async def require(self, voice_id):
        return VoiceOption(id=voice_id, name="Test Voice")


class FakeLiveKitAPI:
    """Records dispatch/room-deletion calls and can fail either boundary."""

    state: dict | None = None

    def __init__(self, *args, **kwargs):
        assert FakeLiveKitAPI.state is not None
        self.state = FakeLiveKitAPI.state
        self.agent_dispatch = SimpleNamespace(create_dispatch=self.dispatch)
        self.room = SimpleNamespace(delete_room=self.delete)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def dispatch(self, request):
        self.state["dispatched"].append(request.room)
        if self.state["dispatch_fails"]:
            raise RuntimeError("dispatch unavailable")

    async def delete(self, request):
        self.state["deleted"].append(request.room)
        if self.state["delete_fails"]:
            raise RuntimeError("room cleanup unavailable")


def build(tmp_path, monkeypatch, **overrides):
    state = {"dispatched": [], "deleted": [], "dispatch_fails": False, "delete_fails": False}
    FakeLiveKitAPI.state = state
    monkeypatch.setattr(livekit_api, "LiveKitAPI", FakeLiveKitAPI)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/lifecycle.db",
        openai_api_key="test-openai",
        deepseek_api_key=overrides.pop("deepseek_api_key", ""),
        default_llm_provider=overrides.pop("default_llm_provider", "openai_direct"),
        deepgram_api_key="test-deepgram",
        eleven_api_key="test-eleven",
        livekit_url="https://fake.livekit",
        livekit_api_key="key" * 8,
        livekit_api_secret="secret" * 8,
        agent_service_secret=AGENT_SECRET,
        demo_calls_per_identity=10,
        _env_file=None,
        **overrides,
    )
    app = create_app(settings, voice_catalog=FakeCatalog())
    return app, TestClient(app), state, tmp_path / "lifecycle.db"


def conversation(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def start(client, conversation_id):
    return client.post("/v1/voice/sessions", json={"conversation_id": conversation_id, "voice_id": "test-voice"})


def reserve_ledger(client):
    response = client.post(
        "/internal/development/reserve", json={"provider": "openai"}, headers={"x-agent-secret": AGENT_SECRET}
    )
    assert response.status_code == 200
    return response.json()["reserved_units"]


def session_row(db_path, session_id):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM voice_sessions WHERE id=?", (session_id,)).fetchone()
    assert row is not None
    return dict(row)


def sessions_with_status(db_path, status):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM voice_sessions WHERE status=?", (status,))]


def expire(db_path, session_id):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            "UPDATE voice_sessions SET expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), session_id),
        )


def test_failed_dispatch_releases_capacity_without_resetting_ledger(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice, closing(TestClient(app)) as bob:
        chat_a = conversation(alice)
        chat_b = conversation(bob)
        reserved = reserve_ledger(alice)

        assert start(alice, chat_a).status_code == 201

        # Bob's failed slot must not stay counted as active.
        state["dispatch_fails"] = True
        failed = start(bob, chat_b)
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "VOICE_DISPATCH_FAILED"
        assert len(sessions_with_status(db_path, "failed")) == 1

        state["dispatch_fails"] = False
        replacement = start(bob, chat_b)
        assert replacement.status_code == 201
        assert state["dispatched"][-1] == session_row(db_path, replacement.json()["id"])["room_name"]
        assert session_row(db_path, replacement.json()["id"])["status"] == "starting"

        # Two active sessions remain the hard cap; the failed one does not add a third.
        # A bare client reuses the running lifespan and must still be closed explicitly.
        with closing(TestClient(app)) as carol:
            assert start(carol, conversation(carol)).status_code == 429
        with closing(sqlite3.connect(db_path)) as connection, connection:
            assert connection.execute("SELECT reserved_units FROM development_budgets").fetchone()[0] == reserved


def test_expired_session_is_reaped_and_frees_capacity(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice, closing(TestClient(app)) as bob:
        chat_a = conversation(alice)
        chat_b = conversation(bob)
        reserved = reserve_ledger(alice)

        first = start(alice, chat_a)
        assert first.status_code == 201
        assert start(bob, chat_b).status_code == 201

        # A bare client reuses the running lifespan and must still be closed explicitly.
        with closing(TestClient(app)) as carol:
            chat_c = conversation(carol)
            assert start(carol, chat_c).status_code == 429

            expire(db_path, first.json()["id"])
            replacement = start(carol, chat_c)
            assert replacement.status_code == 201
            assert session_row(db_path, first.json()["id"])["status"] == "ended"
            # Reaping an expired reservation is not room cleanup; no room delete was invented.
            assert session_row(db_path, first.json()["id"])["room_cleanup_completed_at"] is None
        with closing(sqlite3.connect(db_path)) as connection, connection:
            assert connection.execute("SELECT reserved_units FROM development_budgets").fetchone()[0] == reserved


def test_ending_twice_is_safe_and_cleans_room_once(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        chat = conversation(alice)
        session = start(alice, chat)
        assert session.status_code == 201
        session_id = session.json()["id"]
        room = session_row(db_path, session_id)["room_name"]

        assert alice.post(f"/v1/voice/sessions/{session_id}/end").status_code == 200
        second = alice.post(f"/v1/voice/sessions/{session_id}/end")
        assert second.status_code == 200
        assert second.json()["cleanup"] == "already_complete"
        assert state["deleted"] == [room]


def test_room_cleanup_failure_retries_then_skips_after_success(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        chat = conversation(alice)
        session = start(alice, chat)
        session_id = session.json()["id"]
        state["delete_fails"] = True

        failed = alice.post(f"/v1/voice/sessions/{session_id}/end")
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "ROOM_CLEANUP_FAILED"
        # Capacity is released even when LiveKit room cleanup fails.
        assert session_row(db_path, session_id)["status"] == "ended"

        state["delete_fails"] = False
        retried = alice.post(f"/v1/voice/sessions/{session_id}/end")
        assert retried.status_code == 200
        assert retried.json()["cleanup"] == "complete"
        assert len(state["deleted"]) == 2
        assert alice.post(f"/v1/voice/sessions/{session_id}/end").json()["cleanup"] == "already_complete"
        assert len(state["deleted"]) == 2


def ledger(db_path):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        return connection.execute(
            "SELECT day,provider,reserved_units FROM development_budgets ORDER BY day,provider"
        ).fetchall()


def reconcile_local(settings, *, apply=False, limit=20):
    import asyncio

    from app.services.session_cleanup import reconcile
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def run():
        engine = create_async_engine(settings.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return await reconcile(db, settings, apply=apply, limit=limit)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_absent_room_is_success_but_other_provider_errors_remain_pending(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        identifier = start(alice, conversation(alice)).json()["id"]

        async def absent(self, request):
            self.state["deleted"].append(request.room)
            raise livekit_api.TwirpError("not_found", "absent", status=404)

        monkeypatch.setattr(FakeLiveKitAPI, "delete", absent)
        assert alice.post(f"/v1/voice/sessions/{identifier}/end").json()["cleanup"] == "complete"
        assert session_row(db_path, identifier)["room_cleanup_completed_at"] is not None


def test_provider_authentication_failure_is_not_room_absence(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        identifier = start(alice, conversation(alice)).json()["id"]

        async def denied(self, request):
            raise livekit_api.TwirpError("permission_denied", "denied", status=403)

        monkeypatch.setattr(FakeLiveKitAPI, "delete", denied)
        response = alice.post(f"/v1/voice/sessions/{identifier}/end")
        assert response.status_code == 503
        assert response.json()["detail"]["cleanup"] == "pending"
        assert session_row(db_path, identifier)["room_cleanup_completed_at"] is None


def test_uncertain_dispatch_keeps_reconciling_late_rooms_until_expiry(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    rooms = set()

    async def uncertain(self, request):
        rooms.add(request.room)  # Remote effect succeeded; acknowledgment was lost.
        raise TimeoutError("lost dispatch response")

    async def delete(self, request):
        self.state["deleted"].append(request.room)
        if request.room not in rooms:
            raise livekit_api.TwirpError("not_found", "absent", status=404)
        rooms.remove(request.room)

    monkeypatch.setattr(FakeLiveKitAPI, "dispatch", uncertain)
    monkeypatch.setattr(FakeLiveKitAPI, "delete", delete)
    with alice:
        reserve_ledger(alice)
        before = ledger(db_path)
        response = start(alice, conversation(alice))
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "VOICE_DISPATCH_FAILED"
        failed = sessions_with_status(db_path, "failed")[0]
        assert not rooms  # Best-effort early deletion succeeded.
        assert failed["room_cleanup_completed_at"] is None
        settings = app.state.settings
        assert reconcile_local(settings, apply=True)["pending"] == 1  # Early not-found is not final.
        rooms.add(failed["room_name"])  # Delayed remote creation after early absence.
        expire(db_path, failed["id"])
        assert reconcile_local(settings, apply=True)["completed"] == 1
        assert not rooms
        assert session_row(db_path, failed["id"])["room_cleanup_completed_at"] is not None
        assert ledger(db_path) == before


def test_reconciliation_is_dry_by_default_bounded_and_skips_active(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        chat = conversation(alice)
        first = start(alice, chat).json()["id"]
        state["delete_fails"] = True
        assert alice.post(f"/v1/voice/sessions/{first}/end").status_code == 503
        second = start(alice, chat).json()["id"]
        state["delete_fails"] = False
        prior_attempts = list(state["deleted"])
        assert reconcile_local(app.state.settings)["pending"] == 1
        assert state["deleted"] == prior_attempts
        assert reconcile_local(app.state.settings, apply=True, limit=1)["completed"] == 1
        assert session_row(db_path, second)["status"] == "starting"
        assert session_row(db_path, second)["room_name"] not in state["deleted"]


def test_wrong_owner_cannot_clean_or_bootstrap_terminal_session(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice, closing(TestClient(app)) as bob:
        identifier = start(alice, conversation(alice)).json()["id"]
        conversation(bob)
        assert bob.post(f"/v1/voice/sessions/{identifier}/end").status_code == 404
        assert state["deleted"] == []
        assert alice.post(f"/v1/voice/sessions/{identifier}/end").status_code == 200
        row = session_row(db_path, identifier)
        response = alice.post(
            "/internal/agent/bootstrap",
            headers={"x-agent-secret": AGENT_SECRET, "x-session-id": identifier, "x-room-name": row["room_name"]},
        )
        assert response.status_code == 403
        assert session_row(db_path, identifier)["status"] == "ended"


def test_reconciliation_after_restart_and_expiry(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        first = start(alice, conversation(alice)).json()["id"]
        expire(db_path, first)
        # Admission reaps expired capacity but does not claim cleanup.
        assert start(alice, conversation(alice)).status_code == 201
        assert session_row(db_path, first)["status"] == "ended"
        settings = app.state.settings
    # Restart against the same database, then reconcile from a fresh engine.
    restarted = create_app(settings, voice_catalog=FakeCatalog())
    with TestClient(restarted):
        pass
    assert reconcile_local(settings, apply=True)["completed"] == 1
    assert session_row(db_path, first)["room_cleanup_completed_at"] is not None


def test_concurrent_endings_converge_without_resetting_budget(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        identifier = start(alice, conversation(alice)).json()["id"]
        reserve_ledger(alice)
        before = ledger(db_path)

        def end():
            # Concurrent requests share the running application's one lifespan.
            # Starting/stopping extra lifespans races disposal of its shared engine.
            return alice.post(f"/v1/voice/sessions/{identifier}/end").status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda _: end(), range(2))) == [200, 200]
        assert session_row(db_path, identifier)["room_cleanup_completed_at"] is not None
        assert ledger(db_path) == before
        attempts = len(state["deleted"])
        assert alice.post(f"/v1/voice/sessions/{identifier}/end").json()["cleanup"] == "already_complete"
        assert len(state["deleted"]) == attempts


@pytest.mark.parametrize("error", [TimeoutError("timeout"), ConnectionError("connection")])
def test_transport_failures_leave_retryable_cleanup(tmp_path, monkeypatch, error):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        identifier = start(alice, conversation(alice)).json()["id"]

        async def broken(self, request):
            raise error

        monkeypatch.setattr(FakeLiveKitAPI, "delete", broken)
        response = alice.post(f"/v1/voice/sessions/{identifier}/end")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "ROOM_CLEANUP_FAILED"
        assert session_row(db_path, identifier)["room_cleanup_completed_at"] is None


def test_missing_configuration_and_reconcile_errors_remain_pending(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        identifier = start(alice, conversation(alice)).json()["id"]
        app.state.settings.livekit_api_key = ""
        response = alice.post(f"/v1/voice/sessions/{identifier}/end")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "ROOM_CLEANUP_NOT_CONFIGURED"
        assert state["deleted"] == []
        result = reconcile_local(app.state.settings, apply=True)
        assert result["errors"] == 1 and result["completed"] == 0
        assert session_row(db_path, identifier)["room_cleanup_completed_at"] is None


def test_hung_provider_is_cancelled_by_total_cleanup_deadline(tmp_path, monkeypatch):
    import asyncio

    from app.services import session_cleanup

    app, alice, state, db_path = build(tmp_path, monkeypatch)
    cancelled = []

    async def hung(self, request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(request.room)

    monkeypatch.setattr(FakeLiveKitAPI, "delete", hung)
    monkeypatch.setattr(session_cleanup, "CLEANUP_TIMEOUT_SECONDS", 0.02)
    with alice:
        identifier = start(alice, conversation(alice)).json()["id"]
        response = alice.post(f"/v1/voice/sessions/{identifier}/end")
        assert response.status_code == 503
        assert cancelled
        assert session_row(db_path, identifier)["room_cleanup_completed_at"] is None


def test_funded_default_is_persisted_in_new_session_and_bootstrap(tmp_path, monkeypatch):
    assert Settings(_env_file=None).default_llm_provider == "deepseek_direct"
    app, client, state, db_path = build(
        tmp_path, monkeypatch, default_llm_provider="deepseek_direct", deepseek_api_key="test-deepseek"
    )
    with client:
        chat = conversation(client)
        created = start(client, chat)
        assert created.status_code == 201, created.text
        row = session_row(db_path, created.json()["id"])
        pipeline = __import__("json").loads(row["pipeline"])
        assert pipeline["llm"]["provider"] == "deepseek_direct"
        assert pipeline["llm"]["model"] == "deepseek-flash"
        response = client.post(
            "/internal/agent/bootstrap",
            headers={"x-agent-secret": AGENT_SECRET, "x-session-id": row["id"], "x-room-name": row["room_name"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["pipeline"]["llm"] == pipeline["llm"]


def test_missing_deepseek_key_does_not_silently_select_openai(tmp_path, monkeypatch):
    app, client, state, db_path = build(tmp_path, monkeypatch, default_llm_provider="deepseek_direct")
    with client:
        chat = conversation(client)
        response = start(client, chat)
        assert response.status_code == 503
        assert state["dispatched"] == []
