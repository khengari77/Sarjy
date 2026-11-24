"""Speech-start connection warm-up: API route and worker rate limit."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from warm import ConnectionWarmup

WARM = "/internal/agent/warm"
HEADERS = {"x-agent-secret": "test-service-secret"}


def configured(tmp_path):
    file = tmp_path / "warm.db"
    return Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret=HEADERS["x-agent-secret"],
        _env_file=None,
    )


@contextmanager
def capture_statements():
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", capture)


def test_warm_requires_the_worker_secret_and_issues_one_probe(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        assert client.post(WARM).status_code == 401
        with capture_statements() as statements:
            response = client.post(WARM, headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        # One statement: no turn, session or budget row touched.
        assert len(statements) == 1
        assert statements[0].strip().upper() == "SELECT 1"


def test_warm_jev_failure_is_a_background_best_effort(tmp_path):
    calls: list[str] = []

    class Classifier:
        async def warm(self):
            calls.append("warm")
            raise RuntimeError("provider down")

    with TestClient(create_app(configured(tmp_path), ui_classifier=Classifier())) as client:
        response = client.post(WARM, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls == ["warm"]


class Backend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.paths: list[str] = []

    async def post(self, path: str):
        self.paths.append(path)
        if self.fail:
            raise httpx.ConnectError("backend down")
        return httpx.Response(200, json={"ok": True})


class Llm:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def warm_connection(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")


def speaking(state: str = "speaking"):
    return SimpleNamespace(new_state=state)


def test_speech_start_warms_at_most_once_per_twenty_seconds():
    now = [100.0]
    backend = Backend()
    llm = Llm()

    async def run():
        warmup = ConnectionWarmup(backend, llm, clock=lambda: now[0])
        event = speaking()
        warmup.user_state_changed(event)
        warmup.user_state_changed(event)
        await asyncio.gather(*list(warmup.tasks))
        assert backend.paths == [WARM]
        assert llm.calls == 1

        warmup.user_state_changed(speaking("listening"))
        await asyncio.gather(*list(warmup.tasks))
        assert backend.paths == [WARM]

        now[0] += 19.9
        warmup.user_state_changed(event)
        await asyncio.gather(*list(warmup.tasks))
        assert backend.paths == [WARM]

        now[0] += 0.1
        warmup.user_state_changed(event)
        await asyncio.gather(*list(warmup.tasks))
        assert backend.paths == [WARM, WARM]
        assert llm.calls == 2

    asyncio.run(run())


def test_warm_failures_never_raise():
    backend = Backend(fail=True)
    llm = Llm(fail=True)

    async def run():
        warmup = ConnectionWarmup(backend, llm, clock=lambda: 0.0)
        warmup.user_state_changed(speaking())
        tasks = list(warmup.tasks)
        assert tasks
        await asyncio.gather(*tasks)
        assert backend.paths == [WARM]
        assert llm.calls == 1

    asyncio.run(run())


def test_provider_warmup_never_writes_into_a_live_completion_state():
    import asyncio

    import httpx
    from stream_completion import CompletionState, HttpWarmup, ObservedTransport, completion_state

    def handler(request):
        return httpx.Response(200, json={"data": []})

    async def run():
        client = httpx.AsyncClient(transport=ObservedTransport(httpx.MockTransport(handler)))
        live = CompletionState()
        token = completion_state.set(live)
        try:
            await HttpWarmup(client, "https://example.test/models", "key")()
            assert completion_state.get() is live  # restored
        finally:
            completion_state.reset(token)
            await client.aclose()
        return live

    live = asyncio.run(run())
    assert live == CompletionState()  # untouched
