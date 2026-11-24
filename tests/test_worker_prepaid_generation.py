"""The model's budget reservation starts with the turn instead of after it."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from main import SarjyAgent


class Backend:
    """Records calls and lets a test hold each one open."""

    def __init__(self):
        self.calls = []
        self.gates = {}
        self.refuse = False

    async def post(self, path, json=None):
        self.calls.append((path, dict(json or {})))
        if gate := self.gates.get(path):
            await gate.wait()
        request = httpx.Request("POST", "http://api" + path)
        if path == "/internal/development/reserve" and self.refuse:
            return httpx.Response(429, request=request)
        if path == "/internal/agent/turn/start":
            return httpx.Response(200, request=request, json={"turn_id": "turn-1", "context": {}})
        return httpx.Response(200, request=request, json={})

    def reservations(self):
        return [call for call in self.calls if call[0] == "/internal/development/reserve"]


def agent_for(backend, provider="deepseek_direct"):
    agent = SarjyAgent(backend, {"llm": {"provider": provider}, "max_output_tokens": 128}, "Rules")
    agent.is_command = lambda text: False
    return agent


def message(identifier="m1"):
    return SimpleNamespace(id=identifier, text_content="hello there")


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_the_reservation_runs_while_the_turn_start_is_still_waiting():
    async def scenario():
        backend = Backend()
        backend.gates["/internal/agent/turn/start"] = asyncio.Event()
        agent = agent_for(backend)
        compiling = asyncio.create_task(agent.on_user_turn_completed(None, message()))
        await settle()
        assert [path for path, _ in backend.calls] == ["/internal/agent/turn/start"] or backend.reservations()
        assert backend.reservations() == [("/internal/development/reserve", {"provider": "deepseek", "characters": 0})]
        backend.gates["/internal/agent/turn/start"].set()
        await compiling

    asyncio.run(scenario())


def test_the_model_call_takes_the_prepaid_reservation_instead_of_posting_another():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        await agent.on_user_turn_completed(None, message())
        await agent.reserve("deepseek")
        assert len(backend.reservations()) == 1
        # The prepayment is single use: a fallback or a tool round pays for itself.
        await agent.reserve("deepseek")
        assert len(backend.reservations()) == 2
        assert "llm_reserve_ms" in agent.turn_stages["turn-1"]

    asyncio.run(scenario())


def test_a_refused_prepayment_is_reported_where_the_model_call_needs_it_and_nowhere_else():
    async def scenario():
        backend = Backend()
        backend.refuse = True
        agent = agent_for(backend)
        reported = []

        async def report(exc):
            reported.append(exc.response.status_code)

        await agent.on_user_turn_completed(None, message())
        await settle()
        assert reported == []  # a turn that never calls the model says nothing
        with pytest.raises(httpx.HTTPStatusError):
            await agent.reserve("deepseek", report=report)
        assert reported == [429]

    asyncio.run(scenario())


def test_a_prepayment_for_another_provider_is_not_used_and_the_next_turn_replaces_it():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        await agent.on_user_turn_completed(None, message("m1"))
        await agent.reserve("openai")  # the fallback provider
        await settle()
        assert [call[1]["provider"] for call in backend.reservations()] == ["openai", "deepseek"]
        await agent.on_user_turn_completed(None, message("m2"))
        await agent.on_user_turn_completed(None, message("m3"))
        await settle()
        assert len(backend.reservations()) == 4  # each turn pays once; nothing is posted twice for one turn

    asyncio.run(scenario())


def test_commands_and_unknown_providers_do_not_prepay_and_shutdown_cancels_it():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        agent.is_command = lambda text: True
        await agent.on_user_turn_completed(None, message())
        assert backend.calls == []
        assert agent_for(backend, "something_else").prepaid_generation is None

        held = agent_for(backend)
        backend.gates["/internal/development/reserve"] = asyncio.Event()
        held.is_command = lambda text: False
        held._prepay_generation()
        await settle()
        await held.stop_speech_refills()
        assert held.prepaid_generation is None
        held._prepay_generation()
        assert held.prepaid_generation is None

    asyncio.run(scenario())
