"""The model's budget reservation starts with the turn instead of after it."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from main import SarjyAgent

RESERVE = "/internal/development/reserve"
TURN_START = "/internal/agent/turn/start"


class Backend:
    """Records calls and lets a test hold or refuse each one."""

    def __init__(self):
        self.calls = []
        self.gates = {}
        self.refuse = False

    async def post(self, path, json=None):
        self.calls.append((path, dict(json or {})))
        if gate := self.gates.get(path):
            await gate.wait()
        request = httpx.Request("POST", "http://api" + path)
        if path == RESERVE and self.refuse:
            return httpx.Response(429, request=request)
        if path == TURN_START:
            return httpx.Response(200, request=request, json={"turn_id": "turn-1", "context": {}})
        return httpx.Response(200, request=request, json={})

    def reservations(self, provider=None):
        return [body for path, body in self.calls if path == RESERVE and provider in (None, body["provider"])]


def agent_for(backend, provider="deepseek_direct"):
    agent = SarjyAgent(backend, {"llm": {"provider": provider}, "max_output_tokens": 128}, "Rules")
    agent.is_command = lambda text: False
    return agent


def message(identifier="m1"):
    return SimpleNamespace(id=identifier, text_content="hello there")


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def run(scenario):
    asyncio.run(scenario())


def test_the_reservation_runs_while_the_turn_start_is_still_waiting():
    async def scenario():
        backend = Backend()
        backend.gates[TURN_START] = asyncio.Event()
        agent = agent_for(backend)
        compiling = asyncio.create_task(agent.on_user_turn_completed(None, message()))
        await settle()
        assert backend.reservations() == [{"provider": "deepseek", "characters": 0}]
        assert [path for path, _ in backend.calls].count(TURN_START) == 1
        backend.gates[TURN_START].set()
        await compiling

    run(scenario)


def test_the_model_call_takes_the_prepaid_reservation_once():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        await agent.on_user_turn_completed(None, message())
        await agent.reserve("deepseek")
        assert len(backend.reservations()) == 1
        assert "llm_reserve_ms" in agent.turn_stages["turn-1"]
        # Single use: a tool round or a fallback pays for itself.
        await agent.reserve("deepseek")
        assert len(backend.reservations()) == 2

    run(scenario)


def test_a_refused_prepayment_blocks_the_model_call_and_is_reported_once():
    async def scenario():
        backend = Backend()
        backend.refuse = True
        notices = []

        async def notify(code):
            notices.append(code)

        agent = agent_for(backend)
        agent.notify = notify
        agent._require_deepseek_bounds = lambda request: None
        sent = []

        async def attempt(*args, **kwargs):
            sent.append(args)
            yield None

        agent._stream_attempt = attempt
        await agent.on_user_turn_completed(None, message())
        await settle()
        assert notices == []  # nothing is said until the model call needs the money
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in agent._stream_with_fallback(SimpleNamespace(attempt_id="a"), [], None):
                pass
        assert sent == []  # the provider was never called
        assert notices == ["DAILY_BUDGET_EXHAUSTED"]
        assert len(backend.reservations()) == 1  # no second request was made to retry it

    run(scenario)


def test_a_speech_reservation_in_between_does_not_take_or_lose_the_model_reservation():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        await agent.on_user_turn_completed(None, message())
        await agent.reserve("deepgram_tts", 40)
        await agent.reserve("deepseek")
        await settle()
        assert len(backend.reservations("deepseek")) == 1
        assert len(backend.reservations("deepgram_tts")) == 1

    run(scenario)


def test_a_prepayment_belongs_to_its_turn():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        await agent.on_user_turn_completed(None, message("m1"))
        first = agent.prepaid_generation[2]
        await agent.on_user_turn_completed(None, message("m2"))
        await settle()
        assert first.cancelled() or first.done()
        assert agent.prepaid_generation[1] == "m2"
        # Typed input never ran the hook; an audio turn's prepayment must not pay for it.
        await agent.compile(message("typed"))
        assert agent.prepaid_generation is None
        await agent.reserve("deepseek")
        await settle()
        # m1 was cancelled before it was sent; m2 was sent; the typed turn paid for itself.
        assert len(backend.reservations("deepseek")) == 2

    run(scenario)


def test_the_fallback_provider_pays_for_itself_and_leaves_the_primary_prepayment_alone():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        await agent.on_user_turn_completed(None, message())
        await agent.reserve("openai")
        await settle()
        assert len(backend.reservations("openai")) == 1
        assert len(backend.reservations("deepseek")) == 1
        assert agent.prepaid_generation is not None

    run(scenario)


def test_commands_and_unknown_providers_do_not_prepay_and_shutdown_cancels_everything():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        agent.is_command = lambda text: True
        await agent.on_user_turn_completed(None, message())
        assert backend.calls == []
        other = agent_for(backend, "something_else")
        other._prepay_generation("m1")
        assert other.prepaid_generation is None

        held = agent_for(backend)
        backend.gates[RESERVE] = asyncio.Event()
        held._prepay_generation("m1")
        held._prepay_generation("m2")
        await settle()
        pending = set(held.prepayments)
        await held.stop_speech_refills()
        assert held.prepaid_generation is None and not held.prepayments
        assert all(task.cancelled() or task.done() for task in pending)
        held._prepay_generation("m3")
        assert held.prepaid_generation is None

    run(scenario)


def test_a_newer_turn_does_not_hand_its_prepayment_to_an_older_model_call():
    async def scenario():
        backend = Backend()
        agent = agent_for(backend)
        await agent.on_user_turn_completed(None, message("m1"))  # the older turn is the one being served
        agent._prepay_generation("m2")  # a newer turn's hook runs while m1 waits on something else
        await agent.reserve("deepseek")  # m1's own call
        await settle()
        # m1 paid for itself; its own prepayment was replaced before it was sent; m2's is untouched.
        assert len(backend.reservations("deepseek")) == 2
        assert agent.prepaid_generation is not None and agent.prepaid_generation[1] == "m2"

    run(scenario)
