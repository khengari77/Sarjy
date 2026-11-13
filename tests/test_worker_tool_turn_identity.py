"""Regression for issue #83: tool invocations stay bound to their originating turn.

The risk was reported from static inspection: ``SarjyAgent.current_turn`` is
mutable, and the weather callback derived its operation key from it.  This test
drives the real installed ``livekit-agents`` AgentSession/AgentActivity loop with
fake providers and a fake backend.  Two replies are queued back-to-back so the
later turn compiles before the SDK starts the earlier turn's queued tool task.
That ordering was reproduced with the installed SDK (see the report for the
recorded version and event trace); the callback must still send the earlier
turn's id and operation key.
"""

import asyncio
import importlib.metadata
import json

import httpx
from livekit.agents import AgentSession, llm
from main import SarjyAgent

INSTRUCTIONS = "Protected rules"
CALL_ID = "call-1"
RECORDED_SDK_VERSION = importlib.metadata.version("livekit-agents")


def model_request(turn_id):
    return {
        "turn_id": turn_id,
        "attempt_id": "attempt",
        "instructions": INSTRUCTIONS,
        "selection": {"revision": 1},
        "response_policy": {"tier": 2, "preference": "automatic", "max_output_tokens": 128},
        "settings": {
            "speaking_speed": 1.0,
            "captions_visible": True,
            "approved_voice_id": None,
            "response_preference": "automatic",
            "revision": 0,
        },
        "settings_revision": 0,
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": "Weather?"},
        ],
        "tools": [],
        "tool_history": [],
    }


class FakeStream(llm.LLMStream):
    """First generation emits one weather call; later generations emit text."""

    def __init__(self, model, chat_ctx, tools, conn_options):
        super().__init__(model, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._model = model

    async def _run(self):
        generation = self._model.generation
        self._model.generation += 1
        if generation == 0:
            self._event_ch.send_nowait(
                llm.ChatChunk(
                    id="r",
                    delta=llm.ChoiceDelta(
                        tool_calls=[
                            llm.FunctionToolCall(
                                name="get_current_weather",
                                arguments=json.dumps({"city": "Tripoli", "country": "LY"}),
                                call_id=CALL_ID,
                            )
                        ]
                    ),
                )
            )
        else:
            self._event_ch.send_nowait(llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content="Hello.")))


class FakeLLM(llm.LLM):
    def __init__(self):
        super().__init__()
        self.generation = 0

    def chat(self, *, chat_ctx, tools=None, conn_options=None, **kwargs):
        return FakeStream(self, chat_ctx, list(tools or []), conn_options)


def test_weather_call_is_bound_to_the_turn_that_generated_it():
    compiles = []
    weather_calls = []

    def backend(request):
        path = request.url.path
        if path.endswith("/turn/start"):
            body = json.loads(request.content)
            turn_id = "turn-" + body["request_id"]
            compiles.append(turn_id)
            snapshot = model_request(turn_id)
            return httpx.Response(
                200,
                json={
                    "turn_id": turn_id,
                    "context": {
                        "model_request": snapshot,
                        "revision": 1,
                        "messages": snapshot["messages"],
                    },
                },
            )
        if path.endswith("/turn/finalize") or path.endswith("/reserve"):
            return httpx.Response(200, json={})
        if path.endswith("/tool/weather"):
            weather_calls.append(json.loads(request.content))
            return httpx.Response(200, json={"condition": "sunny"})
        raise AssertionError(path)

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, INSTRUCTIONS)
            session = AgentSession(llm=FakeLLM())
            await session.start(agent)
            # Queue both replies before either runs: the second turn's compile
            # overtakes the first turn's still-queued tool execution task.
            session.generate_reply(user_input="Weather in Tripoli?")
            session.generate_reply(user_input="Hello")
            for _ in range(500):
                if weather_calls:
                    break
                await asyncio.sleep(0.01)
            await session.interrupt()
            await session.aclose()

    asyncio.run(run())

    assert len(compiles) == 2, compiles
    assert compiles[0] != compiles[1]
    assert len(weather_calls) == 1, f"recorded SDK {RECORDED_SDK_VERSION}: {weather_calls}"
    call = weather_calls[0]
    assert call["turn_id"] == compiles[0], f"recorded SDK {RECORDED_SDK_VERSION}; compiles={compiles}; call={call}"
    assert call["request_id"] == f"{compiles[0]}:{CALL_ID}"
    assert call["city"] == "Tripoli"
    assert call["country"] == "LY"


def test_held_invocation_keeps_its_turn_and_key_when_a_later_turn_starts():
    """Hold the old callback, advance a turn, then release it."""

    compiles = []
    weather_calls = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def backend(request):
        path = request.url.path
        if path.endswith("/turn/start"):
            body = json.loads(request.content)
            turn_id = "turn-" + body["request_id"]
            compiles.append(turn_id)
            snapshot = model_request(turn_id)
            return httpx.Response(
                200,
                json={
                    "turn_id": turn_id,
                    "context": {
                        "model_request": snapshot,
                        "revision": 1,
                        "messages": snapshot["messages"],
                    },
                },
            )
        if path.endswith("/turn/finalize") or path.endswith("/reserve"):
            return httpx.Response(200, json={})
        if path.endswith("/tool/weather"):
            weather_calls.append(json.loads(request.content))
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"condition": "sunny"})
        raise AssertionError(path)

    async def run():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, INSTRUCTIONS)
            session = AgentSession(llm=FakeLLM())
            await session.start(agent)
            session.generate_reply(user_input="Weather in Tripoli?")
            for _ in range(500):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            session.generate_reply(user_input="Hello")
            for _ in range(500):
                if len(compiles) >= 2:
                    break
                await asyncio.sleep(0.01)
            release.set()
            await asyncio.sleep(0.05)
            await session.interrupt()
            await session.aclose()

    asyncio.run(run())

    assert len(compiles) == 2, compiles
    assert compiles[0] != compiles[1]
    assert len(weather_calls) == 1, f"recorded SDK {RECORDED_SDK_VERSION}: {weather_calls}"
    assert weather_calls[0]["turn_id"] == compiles[0]
    assert weather_calls[0]["request_id"] == f"{compiles[0]}:{CALL_ID}"
