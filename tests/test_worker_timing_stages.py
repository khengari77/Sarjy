"""The worker records what the API reports and what its own reply path measures."""

import asyncio
from types import SimpleNamespace

import httpx
from main import SarjyAgent

TIMING = {"x-server-ms": "812", "x-db-ms": "455", "x-db-statements": "9"}


def _agent(client):
    return SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")


def test_turn_start_and_command_record_the_servers_own_timing():
    def backend(request):
        if request.url.path.endswith("/command"):
            return httpx.Response(200, headers={"x-server-ms": "640", "x-db-ms": "90"}, json={"command": None})
        return httpx.Response(200, headers=TIMING, json={"turn_id": "turn-1", "context": {}})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            message = SimpleNamespace(id="m1", text_content="Hi")
            await agent.compile(message)
            await agent.command_for(message)
            return agent.turn_stages["turn-1"]

    stages = asyncio.run(run())
    assert stages["turn_start_server_ms"] == 812 and stages["turn_start_db_ms"] == 455
    assert stages["command_server_ms"] == 640 and stages["command_db_ms"] == 90


def test_missing_or_malformed_timing_headers_record_nothing():
    def backend(request):
        return httpx.Response(200, headers={"x-server-ms": "slow", "x-db-ms": ""}, json={"turn_id": "t", "context": {}})

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            await agent.compile(SimpleNamespace(id="m1", text_content="Hi"))
            return agent.turn_stages["t"]

    stages = asyncio.run(run())
    assert "turn_start_server_ms" not in stages and "turn_start_db_ms" not in stages


def test_request_bytes_and_cache_tokens_are_recorded_for_the_turn():
    from main import _record_value

    agent = SimpleNamespace(turn_stages={"t": {}})
    _record_value(agent, "t", "request_bytes", 10_240, first_only=True)
    _record_value(agent, "t", "request_bytes", 99, first_only=True)  # the first request wins
    _record_value(agent, "t", "prompt_cache_hit_tokens", None)  # an absent value records nothing
    _record_value(agent, None, "request_bytes", 1)  # no turn, no record
    assert agent.turn_stages == {"t": {"request_bytes": 10_240}}
