"""Speech text is prepaid: what reaches TTS never exceeds what the ledger granted."""

import asyncio
import json

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from livekit.agents import Agent
from main import SarjyAgent
from speech_budget import BLOCK, LOW_WATER


class Ledger:
    """The reservation route over an in-memory allowance."""

    def __init__(self, allowance=10**9):
        self.allowance = allowance
        self.granted = 0
        self.requests = []
        self.gate = None
        self.status = None

    async def __call__(self, request):

        characters = json.loads(request.content)["characters"]
        self.requests.append(characters)
        if self.gate is not None:
            await self.gate.wait()
        if self.status is not None:
            return httpx.Response(self.status, json={})
        if self.granted + characters > self.allowance:
            return httpx.Response(429, json={})
        self.granted += characters
        return httpx.Response(200, json={})


def speak(monkeypatch, ledger, scenario):
    """Run ``scenario(agent, say, sent, codes)`` with a TTS that records forwarded text."""
    sent, codes = [], []

    async def synthesize(agent, text, model_settings):
        async for part in text:
            sent.append(part)
            assert sum(map(len, sent)) <= ledger.granted
            yield "audio"

    async def notify(code):
        codes.append(code)

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(ledger)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "openai_direct"}}, instructions="x", notify=notify)

            async def say(*parts):
                async def text():
                    for part in parts:
                        yield part

                return [frame async for frame in agent.tts_node(text(), None)]

            await scenario(agent, say, sent, codes)
            await asyncio.gather(*agent.pending, return_exceptions=True)
            await asyncio.sleep(0)
            assert not agent.pending

    monkeypatch.setattr(Agent.default, "tts_node", synthesize)
    asyncio.run(run())


@given(st.lists(st.lists(st.text(max_size=400), max_size=6), max_size=4), st.integers(min_value=0, max_value=900))
def test_forwarded_text_is_an_in_order_prefix_within_the_grant(replies, allowance):
    ledger = Ledger(allowance)
    with pytest.MonkeyPatch.context() as monkeypatch:

        async def scenario(agent, say, sent, codes):
            spoken = ""
            for reply in replies:
                before = len(sent)
                try:
                    await say(*reply)
                except httpx.HTTPStatusError:
                    assert "".join(reply).startswith("".join(sent[before:]))
                    assert codes[-1] == "DAILY_BUDGET_EXHAUSTED"
                    return
                spoken += "".join(reply)
                assert "".join(sent) == spoken

        speak(monkeypatch, ledger, scenario)
    assert ledger.granted <= allowance


def test_short_text_is_forwarded_before_the_reply_ends(monkeypatch):
    ledger = Ledger()

    async def scenario(agent, say, sent, codes):
        release = asyncio.Event()

        async def text():
            yield "Hi"
            await release.wait()
            yield " there"

        frames = agent.tts_node(text(), None)
        assert await anext(frames) == "audio"
        assert sent == ["Hi"]
        release.set()
        assert [frame async for frame in frames] == ["audio"]
        assert sent == ["Hi", " there"]

    speak(monkeypatch, ledger, scenario)


def test_unspent_characters_carry_over_without_another_request(monkeypatch):
    ledger = Ledger()

    async def scenario(agent, say, sent, codes):
        await say("a" * 40)
        await say("b" * 40)
        assert ledger.requests == [BLOCK]

    speak(monkeypatch, ledger, scenario)


def test_silent_inference_reserves_nothing(monkeypatch):
    ledger = Ledger()

    async def scenario(agent, say, sent, codes):
        assert await say() == []
        assert await say("", "") == []

    speak(monkeypatch, ledger, scenario)
    assert ledger.requests == []


def test_refill_runs_ahead_and_does_not_hold_covered_text(monkeypatch):
    ledger = Ledger()

    async def scenario(agent, say, sent, codes):
        await say("a" * (BLOCK - LOW_WATER))
        assert ledger.requests == [BLOCK]
        ledger.gate = asyncio.Event()
        # Crosses the low-water mark: the refill is in flight and blocked, the text is not.
        assert await say("b" * LOW_WATER) == ["audio"]
        await asyncio.sleep(0)
        ledger.gate.set()

    speak(monkeypatch, ledger, scenario)
    assert ledger.requests == [BLOCK, BLOCK]


def test_refused_refill_is_silent_until_its_text_is_needed(monkeypatch):
    ledger = Ledger(allowance=BLOCK)

    async def scenario(agent, say, sent, codes):
        await say("a" * BLOCK)
        await asyncio.gather(*agent.pending, return_exceptions=True)
        assert codes == []
        with pytest.raises(httpx.HTTPStatusError):
            await say("b")
        assert codes == ["DAILY_BUDGET_EXHAUSTED"]
        assert "b" not in "".join(sent)

    speak(monkeypatch, ledger, scenario)


def test_text_that_fits_the_remaining_allowance_is_still_spoken(monkeypatch):
    ledger = Ledger(allowance=BLOCK - 1)

    async def scenario(agent, say, sent, codes):
        await say("hello")
        assert sent == ["hello"] and codes == []

    speak(monkeypatch, ledger, scenario)
    assert ledger.requests == [BLOCK, 5]


def test_service_failure_is_reported_once_and_not_retried(monkeypatch):
    ledger = Ledger()
    ledger.status = 503

    async def scenario(agent, say, sent, codes):
        with pytest.raises(httpx.HTTPStatusError):
            await say("hello")
        assert codes == ["BUDGET_SERVICE_UNAVAILABLE"] and sent == []

    speak(monkeypatch, ledger, scenario)
    assert ledger.requests == [BLOCK]


def test_interrupted_wait_keeps_the_charged_grant_for_the_next_reply(monkeypatch):
    ledger = Ledger()

    async def scenario(agent, say, sent, codes):
        ledger.gate = asyncio.Event()
        reply = asyncio.create_task(say("interrupted"))
        while not ledger.requests:
            await asyncio.sleep(0)
        reply.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reply
        ledger.gate.set()
        await say("next")
        assert sent == ["next"] and ledger.requests == [BLOCK]

    speak(monkeypatch, ledger, scenario)


def test_overlapping_replies_share_one_refill(monkeypatch):
    ledger = Ledger()

    async def scenario(agent, say, sent, codes):
        await asyncio.gather(say("a" * 30), say("b" * 30))
        assert ledger.requests == [BLOCK]

    speak(monkeypatch, ledger, scenario)


def test_transport_failure_reaches_the_caller(monkeypatch):
    async def broken(request):
        raise httpx.ConnectError("down")

    ledger = Ledger()

    async def scenario(agent, say, sent, codes):
        agent.backend = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(broken))
        async with agent.backend:
            with pytest.raises(httpx.ConnectError):
                await say("hello")
        assert sent == [] and codes == []

    speak(monkeypatch, ledger, scenario)
