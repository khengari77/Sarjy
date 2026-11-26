import asyncio
from types import SimpleNamespace

import httpx
import provider_factory
import pytest
from app.main import engine_options
from main import SarjyAgent, backend_client


def _agent(client):
    return SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")


def _message(identifier="turn-1"):
    return SimpleNamespace(id=identifier, text_content="Hi")


def test_worker_backend_client_keeps_connections_alive():
    async def run():
        client = backend_client("http://api", {"x-agent-secret": "secret"})
        try:
            assert client._transport.transport._pool._keepalive_expiry == 50
        finally:
            await client.aclose()

    asyncio.run(run())


def test_turn_start_retries_once_on_a_transport_error():
    calls = []

    def backend(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ReadError("dropped")
        return httpx.Response(200, json={"turn_id": "turn-1", "context": {}})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            await agent.compile(_message())
            assert list(agent.prepared) == ["turn-1"]
            assert agent.current_turn == "turn-1"
            assert list(agent.turn_stages) == ["turn-1"]
            assert "turn_start_ms" in agent.turn_stages["turn-1"]

    asyncio.run(run())
    assert calls == ["/internal/agent/turn/start", "/internal/agent/turn/start"]


@pytest.mark.parametrize("status", [500, 403])
def test_turn_start_does_not_retry_an_http_status_error(status):
    calls = []

    def backend(request):
        calls.append(request.url.path)
        return httpx.Response(status, json={})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            with pytest.raises(httpx.HTTPStatusError):
                await agent.compile(_message())

    asyncio.run(run())
    assert calls == ["/internal/agent/turn/start"]


def test_turn_start_raises_after_two_transport_errors():
    calls = []

    def backend(request):
        calls.append(request.url.path)
        raise httpx.RemoteProtocolError("server disconnected")

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            with pytest.raises(httpx.RemoteProtocolError):
                await agent.compile(_message())
            assert agent.prepared == {}

    asyncio.run(run())
    assert calls == ["/internal/agent/turn/start", "/internal/agent/turn/start"]


def test_an_unreachable_server_is_not_retried():
    calls = []

    def backend(request):
        calls.append(request.url.path)
        raise httpx.ConnectError("unreachable")

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            with pytest.raises(httpx.ConnectError):
                await _agent(client).compile(_message())

    asyncio.run(run())
    assert calls == ["/internal/agent/turn/start"]


def test_provider_transport_keeps_idle_connections(monkeypatch):
    captured = {}

    def sdk(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(provider_factory.openai_sdk, "AsyncOpenAI", sdk)
    monkeypatch.setattr(provider_factory, "OwnedDeepSeekLLM", lambda **kwargs: SimpleNamespace(**kwargs))

    provider_factory.build_llm(
        {"llm": {"provider": "deepseek_direct", "model": "deepseek-flash"}},
        {"DEEPSEEK_API_KEY": "test"},
    )
    http_client = captured["http_client"]
    assert http_client._transport.transport._pool._keepalive_expiry == 50

    async def close():
        await http_client.aclose()

    asyncio.run(close())


def test_engine_options_only_tune_non_sqlite():
    assert engine_options("sqlite+aiosqlite:///worker.db") == {}
    assert engine_options("postgresql+asyncpg://user:pass@pooler:6543/db") == {
        "pool_recycle": 240,
    }


def test_a_timeout_is_not_retried():
    calls = []

    def backend(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout("slow")

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            with pytest.raises(httpx.ReadTimeout):
                await _agent(client).compile(_message())

    asyncio.run(run())
    assert calls == ["/internal/agent/turn/start"]


def test_the_default_provider_transport_keeps_idle_connections_shorter_than_servers_do():
    from stream_completion import KEEPALIVE_LIMITS, ObservedTransport

    assert KEEPALIVE_LIMITS.keepalive_expiry == 50
    assert ObservedTransport().transport._pool._keepalive_expiry == 50


def test_pool_warm_up_is_bounded_and_never_raises(monkeypatch):
    import time

    from app.main import _warm_pool

    monkeypatch.setattr("app.main.POOL_WARM_TIMEOUT_SECONDS", 0.1)

    class Hung:
        def connect(self):
            return self

        async def __aenter__(self):
            await asyncio.sleep(30)

        async def __aexit__(self, *exc):
            return False

    class Broken(Hung):
        async def __aenter__(self):
            raise ConnectionError("down")

    started = time.perf_counter()
    asyncio.run(_warm_pool(Hung()))
    assert time.perf_counter() - started < 6
    asyncio.run(_warm_pool(Broken()))
