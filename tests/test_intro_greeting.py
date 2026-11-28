import asyncio
import json
import logging
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import main as worker
import pytest
from app.domain.intro import INTRO, SHORT_GREETING, intro_for
from app.main import create_app
from fastapi.testclient import TestClient
from test_voices import configured, livekit_control_plane  # noqa: F401


def test_intro_text_is_one_constant_per_language_and_fits_one_reservation():
    assert set(INTRO) == set(SHORT_GREETING) == {"en", "ar"}
    assert all(len(text) < 500 for text in INTRO.values())
    assert 35 <= len(INTRO["en"].split()) <= 60


def test_intro_for_full_short_and_unspeakable_cases():
    assert intro_for("en", seen=False, tts_provider="deepgram_direct") == {"kind": "full", "text": INTRO["en"]}
    assert intro_for("en", seen=True, tts_provider="deepgram_direct") == {
        "kind": "short",
        "text": SHORT_GREETING["en"],
    }
    # Aura-2 has no Arabic: skip silently rather than speak English over an Arabic call.
    assert intro_for("ar", seen=False, tts_provider="deepgram_direct") is None
    assert intro_for("fr", seen=False, tts_provider=None) is None


@pytest.mark.usefixtures("livekit_control_plane")
def test_bootstrap_flag_is_per_identity_and_set_only_by_the_played_callback(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]

        def call():
            session = client.post(
                "/v1/voice/sessions", json={"conversation_id": chat, "voice_id": "aura-2-apollo-en"}
            ).json()
            with closing(sqlite3.connect(tmp_path / "voice.db")) as db:
                room = db.execute("SELECT room_name FROM voice_sessions WHERE id=?", (session["id"],)).fetchone()[0]
            headers = {"x-agent-secret": "worker-secret", "x-session-id": session["id"], "x-room-name": room}
            return session, headers

        first, headers = call()
        assert client.post("/internal/agent/bootstrap", headers=headers).json()["intro"] == {
            "kind": "full",
            "text": INTRO["en"],
        }
        # A bootstrap alone (call dropped, intro interrupted) must not consume the introduction.
        assert client.post(f"/v1/voice/sessions/{first['id']}/end").status_code == 200
        second, headers = call()
        assert client.post("/internal/agent/bootstrap", headers=headers).json()["intro"]["kind"] == "full"
        assert client.post("/internal/agent/intro-played", headers=headers).json() == {"ok": True}
        assert client.post("/internal/agent/intro-played", headers=headers).json() == {"ok": True}
        assert client.post("/internal/agent/bootstrap", headers=headers).json()["intro"] == {
            "kind": "short",
            "text": SHORT_GREETING["en"],
        }
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db:
            assert db.execute("SELECT revision FROM user_settings").fetchone() == (0,)
        assert client.post(f"/v1/voice/sessions/{second['id']}/end").status_code == 200
        assert (
            client.post("/internal/agent/intro-played", headers={**headers, "x-agent-secret": "no"}).status_code == 401
        )


class FakeHandle:
    def __init__(self, interrupted=False, error=None):
        self.interrupted, self._error = interrupted, error

    async def wait_for_playout(self):
        return None

    def exception(self):
        return self._error


class FakeSession:
    def __init__(self, handle=None):
        self.said, self.handle = [], handle or FakeHandle()

    def say(self, text, **kwargs):
        self.said.append((text, kwargs))
        return self.handle


def greet(session, intro, user_arrives=True):
    marks = []

    def upstream(request):
        marks.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    async def user():
        if not user_arrives:
            await asyncio.sleep(60)

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(upstream)) as backend:
            worker.INTRO_WAIT_SECONDS = 0.05 if not user_arrives else 15
            return await worker.play_greeting(session, backend, intro, user)

    return asyncio.run(run()), marks


FULL = {"kind": "full", "text": INTRO["en"]}


def test_first_call_speaks_deterministically_then_marks_seen():
    session = FakeSession()
    result, marks = greet(session, FULL)
    assert result == "played" and marks == ["/internal/agent/intro-played"]
    text, kwargs = session.said[0]
    assert text == INTRO["en"] and kwargs == {"allow_interruptions": True, "add_to_chat_ctx": False}


def test_second_call_speaks_only_the_short_line_and_never_marks():
    session = FakeSession()
    result, marks = greet(session, {"kind": "short", "text": SHORT_GREETING["en"]})
    assert result == "played" and marks == [] and session.said[0][0] == SHORT_GREETING["en"]


def test_interrupted_or_failed_intro_is_not_marked_seen():
    for handle in (FakeHandle(interrupted=True), FakeHandle(error=ValueError("DAILY_BUDGET_EXHAUSTED"))):
        result, marks = greet(FakeSession(handle), FULL)
        assert result == "interrupted" and marks == []


def test_no_intro_or_no_user_or_stopped_session_says_nothing_and_never_raises():
    assert greet(FakeSession(), None) == ("skipped", [])
    silent = FakeSession()
    assert greet(silent, FULL, user_arrives=False) == ("not_played", []) and silent.said == []

    class Stopped(FakeSession):
        def say(self, text, **kwargs):
            raise RuntimeError("AgentSession isn't running")

    assert greet(Stopped(), FULL) == ("not_played", [])


def test_call_start_stages_are_logged_once_since_job_accept(caplog):
    stages: dict[str, int | None] = {"bootstrap_done": 12, "session_started": 34}
    call_started = time.perf_counter() - 0.05

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as backend:
            return await worker.play_greeting(
                FakeSession(), backend, FULL, lambda: asyncio.sleep(0), stages=stages, call_started=call_started
            )

    with caplog.at_level(logging.INFO):
        assert asyncio.run(run()) == "played"

    records = [record for record in caplog.records if record.getMessage().startswith("call_start_stages ")]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage().split(" ", 1)[1])
    for key in ("bootstrap_done", "session_started", "participant_joined", "greeting_say_called", "greeting_done"):
        value = payload[key]
        assert isinstance(value, int)
        assert value >= 0
    assert records[0].bootstrap_done == payload["bootstrap_done"]  # extra fields stay available


def test_failed_mark_is_reported_without_breaking_the_call():
    session = FakeSession()

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(500))
        async with httpx.AsyncClient(base_url="http://test", transport=transport) as backend:
            return await worker.play_greeting(session, backend, FULL, lambda: asyncio.sleep(0))

    assert asyncio.run(run()) == "mark_failed"


def test_entrypoint_starts_the_greeting_after_the_session_runs(monkeypatch):
    events = []
    room = SimpleNamespace(remote_participants={}, local_participant=SimpleNamespace(), on=lambda event: lambda cb: cb)
    room.local_participant.register_rpc_method = lambda name: lambda function: function
    ctx = SimpleNamespace(
        job=SimpleNamespace(metadata=json.dumps({"session_id": "s"}), room=SimpleNamespace(name="r")),
        room=room,
        shutdown=lambda: events.append("shutdown"),
    )

    async def connect():
        events.append("connected")

    async def wait_for_participant():
        events.append("user")

    ctx.connect, ctx.wait_for_participant = connect, wait_for_participant

    class Session(FakeSession):
        def __init__(self, **kwargs):
            super().__init__()

        def on(self, event, callback=None):
            return callback

        async def start(self, agent, **kwargs):
            events.append("session-start")

        async def aclose(self):
            events.append("session-close")

    session_holder = []
    monkeypatch.setattr(worker, "AgentSession", lambda **kw: session_holder.append(Session()) or session_holder[-1])

    def upstream(request):
        if request.url.path == "/internal/agent/intro-played":
            events.append("marked")
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            200,
            json={
                "pipeline": {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
                "instructions": "x",
                "expires_at": (datetime.now(UTC) + timedelta(seconds=0.5)).isoformat(),
                "intro": FULL,
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        worker.httpx, "AsyncClient", lambda **kw: original(**{**kw, "transport": httpx.MockTransport(upstream)})
    )
    monkeypatch.setattr(worker, "build_providers", lambda config, credentials: {})
    ctx.proc = SimpleNamespace(userdata={"vad": object()})
    monkeypatch.setattr(
        worker,
        "read_worker_environment",
        lambda: {
            "API_INTERNAL_URL": "http://test",
            "AGENT_SERVICE_SECRET": "s",
            "DEEPGRAM_API_KEY": "k",
            "OPENAI_API_KEY": "k",
        },
    )
    asyncio.run(worker.entrypoint(ctx))
    assert events.index("session-start") < events.index("user") < events.index("marked") < events.index("shutdown")
    assert session_holder[0].said[0][0] == INTRO["en"]
