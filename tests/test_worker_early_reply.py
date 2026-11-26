"""The model starts while the command answer is pending; only the answer decides what is heard."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from livekit.agents import llm
from main import SarjyAgent
from test_worker_commands import _command, _snapshot
from wordle_tools import WORDLE_MODE_NUDGE

COMMAND = _command("executed", "SetCaptionsVisible", "Captions hidden.")


def said(text):
    return llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content=text))


class Worker:
    """A worker whose command answer and model are controlled by the test."""

    def __init__(self, monkeypatch, answer, *, refuse=False, **agent_kwargs):
        self.answer, self.refuse = answer, refuse
        self.agent_kwargs = agent_kwargs
        self.gate = asyncio.Event()
        self.model_started = asyncio.Event()
        self.model_gate = asyncio.Event()
        self.model_gate.set()
        self.choices, self.closed, self.codes, self.published = [], 0, [], []
        self.reservations = 0
        monkeypatch.setattr(SarjyAgent, "session", property(lambda _: SimpleNamespace(current_speech=None, llm=None)))

    async def backend(self, request):
        path = request.url.path
        if path.endswith("/turn/start"):
            return httpx.Response(200, json=_snapshot())
        if path.endswith("/command"):
            await self.gate.wait()
            return httpx.Response(200, json=self.answer)
        if path.endswith("/reserve"):
            self.reservations += 1
            return httpx.Response(429 if self.refuse else 200, json={})
        raise AssertionError(path)

    async def attempt(self, provider, kind, request, tools, tool_choice):
        self.choices.append(tool_choice)
        self.model_started.set()
        try:
            yield SimpleNamespace(kind="text", text="hello")
            await self.model_gate.wait()
            yield SimpleNamespace(kind="text", text="world")
        finally:
            self.closed += 1

    async def run(self, scenario):
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(self.backend)) as client:
            agent = SarjyAgent(
                client,
                {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128},
                "Rules",
                notify=self.notify,
                publish=self.publish,
                **self.agent_kwargs,
            )
            agent._stream_attempt = self.attempt
            context = llm.ChatContext()
            context.add_message(role="user", content="Hello there.", id="m")
            await scenario(agent, context)
        assert len(asyncio.all_tasks()) == 1

    async def notify(self, code):
        self.codes.append(code)

    async def publish(self, command):
        self.published.append(command)


@pytest.fixture(autouse=True)
def plain_chunks(monkeypatch):
    monkeypatch.setattr("main.livekit_chunk", lambda event: said(event.text))


async def heard(agent, context, tools=()):
    return [chunk.delta.content async for chunk in agent.llm_node(context, list(tools), None)]


def test_the_model_runs_before_the_answer_and_nothing_is_heard_until_it(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None})

        async def scenario(agent, context):
            reply = asyncio.create_task(heard(agent, context))
            await asyncio.wait_for(worker.model_started.wait(), 1)
            await asyncio.sleep(0.01)
            assert not reply.done()
            worker.gate.set()
            assert await reply == ["hello", "world"]
            assert worker.choices == [None] and worker.reservations == 1 and worker.closed == 1

        await worker.run(scenario)

    asyncio.run(main())


def test_the_first_words_follow_the_answer_without_waiting_for_the_rest(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None})
        worker.model_gate.clear()
        worker.gate.set()

        async def scenario(agent, context):
            stream = agent.llm_node(context, [], None)
            first = await asyncio.wait_for(anext(stream), 1)
            assert first.delta.content == "hello"
            await stream.aclose()
            assert worker.closed == 1

        await worker.run(scenario)

    asyncio.run(main())


def test_a_command_speaks_alone_even_when_the_model_already_answered(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": COMMAND})

        async def scenario(agent, context):
            reply = asyncio.create_task(heard(agent, context))
            await worker.model_started.wait()
            worker.gate.set()
            assert await reply == ["Captions hidden."]
            assert worker.published == [COMMAND] and worker.closed == 1

        await worker.run(scenario)

    asyncio.run(main())


@pytest.mark.parametrize("command", [None, COMMAND])
def test_a_refused_reservation_is_announced_only_if_its_reply_is_used(monkeypatch, command):
    async def main():
        worker = Worker(monkeypatch, {"command": command}, refuse=True)

        async def scenario(agent, context):
            reply = asyncio.create_task(heard(agent, context))
            while not worker.reservations:
                await asyncio.sleep(0)
            await asyncio.sleep(0.01)
            assert worker.codes == []
            worker.gate.set()
            if command is None:
                with pytest.raises(httpx.HTTPStatusError):
                    await reply
                assert worker.codes == ["DAILY_BUDGET_EXHAUSTED"]
            else:
                assert await reply == ["Captions hidden."]
                assert worker.codes == []

        await worker.run(scenario)

    asyncio.run(main())


def test_interruption_while_waiting_stops_the_model(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None})
        worker.model_gate.clear()

        async def scenario(agent, context):
            reply = asyncio.create_task(heard(agent, context))
            await worker.model_started.wait()
            reply.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reply
            assert worker.closed == 1

        await worker.run(scenario)

    asyncio.run(main())


@pytest.mark.parametrize("reason", ["asked", "blocked", "outcome", "forced", "question", "wordle"])
def test_turns_the_answer_or_application_owns_do_not_start_early(monkeypatch, reason):
    async def main():
        worker = Worker(monkeypatch, {"command": COMMAND})

        async def scenario(agent, context):
            await agent.compile(context.items[-1])
            if reason == "asked":
                agent.commands["m"] = None
            elif reason == "blocked":
                agent.command_blocked_turn = "m"
            elif reason == "outcome":
                agent.memory_outcome = "save"
            elif reason == "forced":
                agent.forced_speech = ("another turn", "Should I?")
                early = agent._start_reply_early(
                    context.items[-1], await agent.compile(context.items[-1]), context, [], None
                )
                assert early is not None
                await early.discard()
                agent.forced_speech = (agent.current_turn, "Should I?")
            elif reason == "question":
                agent.memory_proposal = {"confirmation_id": "c", "confirmation_revision": 1, "question": "Keep it?"}
            else:
                agent.wordle_active = lambda: True
            user = context.items[-1]
            assert agent._start_reply_early(user, await agent.compile(user), context, [], None) is None

        await worker.run(scenario)

    asyncio.run(main())


def test_a_held_tool_call_is_not_handed_over_on_a_command_turn(monkeypatch):
    call = llm.FunctionToolCall(arguments="{}", name="get_current_weather", call_id="c")
    monkeypatch.setattr(
        "main.livekit_chunk", lambda event: llm.ChatChunk(id="r", delta=llm.ChoiceDelta(tool_calls=[call]))
    )

    async def main():
        worker = Worker(monkeypatch, {"command": COMMAND})

        async def scenario(agent, context):
            async def collect():
                return [chunk async for chunk in agent.llm_node(context, [], None)]

            reply = asyncio.create_task(collect())
            await worker.model_started.wait()
            await asyncio.sleep(0.01)
            worker.gate.set()
            chunks = await reply
            assert [chunk.delta.content for chunk in chunks] == ["Captions hidden."]
            assert not any(chunk.delta.tool_calls for chunk in chunks)

        await worker.run(scenario)

    asyncio.run(main())


@pytest.mark.parametrize("change", ["wordle", "outcome"])
def test_a_reply_asked_for_too_early_is_asked_for_again(monkeypatch, change):
    async def main():
        worker = Worker(monkeypatch, {"command": None})

        async def scenario(agent, context):
            reply = asyncio.create_task(heard(agent, context))
            await worker.model_started.wait()
            if change == "wordle":
                agent.wordle_active = lambda: True
            else:
                agent.memory_outcome = "save"
            worker.gate.set()
            if change == "wordle":
                # The popup opened mid-turn: Wordle mode answers "Hello there." with the nudge, and the
                # early reply (asked for before the popup opened) is closed and never heard.
                assert await reply == [WORDLE_MODE_NUDGE]
                assert len(worker.choices) == 1 and worker.closed == 1
                return
            assert await reply == ["hello", "world"]
            assert len(worker.choices) == 2 and worker.closed == 2

        await worker.run(scenario)

    asyncio.run(main())
