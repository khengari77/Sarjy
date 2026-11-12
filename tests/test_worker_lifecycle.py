import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import main as worker


def test_worker_connects_before_registering_rpc_and_closes_session(monkeypatch):
    events = []

    class Room:
        remote_participants = {}

        def on(self, event):
            return lambda callback: callback

        @property
        def local_participant(self):
            assert "connected" in events
            return self

        def register_rpc_method(self, name):
            events.append("rpc")
            return lambda function: function

    room = Room()

    class Context:
        job = SimpleNamespace(metadata=json.dumps({"session_id": "session"}), room=SimpleNamespace(name="room"))

        async def connect(self):
            events.append("connected")

        def shutdown(self):
            events.append("shutdown")

    ctx = Context()
    ctx.room = room

    class Session:
        def __init__(self, **kwargs):
            pass

        async def start(self, agent, **kwargs):
            assert events.index("connected") < events.index("rpc")
            assert kwargs["room_options"].close_on_disconnect is False
            events.append("session-start")

        async def aclose(self):
            events.append("session-close")

    def upstream(request):
        assert request.headers["x-agent-secret"] == "test-secret"
        return httpx.Response(
            200,
            json={
                "pipeline": {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
                "instructions": "Test instructions.",
                "expires_at": datetime.now(UTC).isoformat(),
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        worker.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(upstream))
    )
    monkeypatch.setattr(worker, "AgentSession", Session)
    monkeypatch.setattr(worker, "build_providers", lambda config, credentials: {})
    monkeypatch.setattr(worker.silero.VAD, "load", lambda: None)
    monkeypatch.setattr(
        worker,
        "read_worker_environment",
        lambda: {
            "API_INTERNAL_URL": "http://test",
            "AGENT_SERVICE_SECRET": "test-secret",
            "DEEPGRAM_API_KEY": "test",
            "ELEVEN_API_KEY": "test",
            "OPENAI_API_KEY": "test",
        },
    )
    asyncio.run(worker.entrypoint(ctx))
    assert events == ["connected", "rpc", "session-start", "session-close", "shutdown"]
