"""A slow reply gets one deterministic spoken filler; nothing is persisted or replayed as answer."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from config import WORKER_KEYS, reassurance_seconds
from generation import GenerationEvent
from livekit.agents import llm
from main import SarjyAgent, _continuation, _without_leading_filler
from reassurance import PHRASES, ReassuranceTimer
from test_worker_commands import _command, _snapshot
from test_worker_early_reply import Worker

COMMAND = _command("executed", "SetCaptionsVisible", "Captions hidden.")


def text_event(text, attempt_id="a"):
    return GenerationEvent(kind="text", turn_id="turn", attempt_id=attempt_id, text=text)


def tool_event(name, attempt_id="a"):
    return GenerationEvent(kind="tool", turn_id="turn", attempt_id=attempt_id, call_id="c", name=name, arguments="{}")


def context(text="Hello there.", message_id="m"):
    result = llm.ChatContext()
    result.add_message(role="user", content=text, id=message_id)
    return result


async def start_turn(agent, chat_ctx):
    await agent.on_user_turn_completed(chat_ctx, chat_ctx.items[-1])
    while agent.prepayments:
        await asyncio.sleep(0)


async def heard(agent, chat_ctx):
    return [chunk async for chunk in agent.llm_node(chat_ctx, [], None)]


async def spoken(agent, chat_ctx):
    return [chunk.delta.content async for chunk in agent.llm_node(chat_ctx, [], None) if chunk.delta.content]


def slow_attempt(delay=0.2):
    async def attempt(_provider, _kind, _request, _tools, _tool_choice):
        await asyncio.sleep(delay)
        yield text_event("hello")
        yield text_event("world")

    return attempt


def tool_attempt(name):
    async def attempt(_provider, _kind, _request, _tools, _tool_choice):
        yield tool_event(name)

    return attempt


def test_a_fast_reply_gets_no_filler(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None}, reassurance_seconds=1.0)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()

            async def attempt(_provider, _kind, _request, _tools, _tool_choice):
                yield text_event("hello")
                yield text_event("world")

            agent._stream_attempt = attempt
            assert await spoken(agent, chat_ctx) == ["hello", "world"]

        await worker.run(scenario)

    asyncio.run(main())


def test_a_slow_reply_gets_one_filler_then_the_real_chunks(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None}, reassurance_seconds=0.05)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()
            agent._stream_attempt = slow_attempt()
            chunks = await spoken(agent, chat_ctx)
            assert chunks[0].strip() in PHRASES
            assert chunks[1:] == ["hello", "world"]

        await worker.run(scenario)

    asyncio.run(main())


def test_a_tool_call_gets_the_filler_immediately(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None}, reassurance_seconds=1.0)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()
            agent._stream_attempt = tool_attempt("get_current_weather")
            chunks = [chunk async for chunk in agent.llm_node(chat_ctx, [], None)]
            assert chunks[0].delta.content.strip() in PHRASES
            assert chunks[1].delta.tool_calls[0].name == "get_current_weather"

        await worker.run(scenario)

    asyncio.run(main())


def test_a_wordle_tool_does_not_get_the_filler(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None}, reassurance_seconds=1.0)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()
            agent._stream_attempt = tool_attempt("play_wordle")
            chunks = [chunk async for chunk in agent.llm_node(chat_ctx, [], None)]
            assert chunks[0].delta.content is None
            assert chunks[0].delta.tool_calls[0].name == "play_wordle"

        await worker.run(scenario)

    asyncio.run(main())


def test_a_command_turn_never_gets_the_filler(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": COMMAND}, reassurance_seconds=1.0)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()
            agent._stream_attempt = slow_attempt()
            assert await spoken(agent, chat_ctx) == ["Captions hidden."]

        await worker.run(scenario)

    asyncio.run(main())


@pytest.mark.parametrize("reason", ["forced", "decision", "blocked", "outcome", "question", "wordle"])
def test_turns_owned_by_the_application_never_get_the_filler(monkeypatch, reason):
    async def main():
        worker = Worker(monkeypatch, {"command": None}, reassurance_seconds=0.05)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()
            agent._stream_attempt = slow_attempt()
            if reason == "forced":
                agent.forced_speech = (agent.current_turn, "Should I?")
            elif reason == "decision":
                agent.decision_turn = agent.current_turn
            elif reason == "blocked":
                agent.command_blocked_turn = "m"
            elif reason == "outcome":
                agent.memory_outcome = "save"
            elif reason == "question":
                agent.memory_proposal = {
                    "confirmation_id": "c",
                    "confirmation_revision": 1,
                    "question": "Keep it?",
                }
            else:
                agent.wordle_active = lambda: True
            chunks = await spoken(agent, chat_ctx)
            assert not any(chunk in PHRASES for chunk in chunks)

        await worker.run(scenario)

    asyncio.run(main())


def test_threshold_zero_disables_the_filler(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None}, reassurance_seconds=0)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()
            agent._stream_attempt = slow_attempt()
            assert await spoken(agent, chat_ctx) == ["hello", "world"]

        await worker.run(scenario)

    asyncio.run(main())


def test_phrases_rotate_in_round_robin_order():
    timer = ReassuranceTimer(1.0)
    first = []
    for turn in ("t1", "t2", "t3", "t4", "t5"):
        timer.begin(turn, 0.0)
        first.append(timer.fire(turn))
    assert first == list(PHRASES) + [PHRASES[0]]
    assert timer.fire("t1") is None  # at most once per turn


def test_the_threshold_is_configured_safely():
    assert reassurance_seconds({}) == 1.6
    assert reassurance_seconds({"REASSURANCE_AFTER_SECONDS": "0"}) == 0
    assert reassurance_seconds({"REASSURANCE_AFTER_SECONDS": "0.05"}) == 0.05
    assert reassurance_seconds({"REASSURANCE_AFTER_SECONDS": "11"}) == 0
    assert reassurance_seconds({"REASSURANCE_AFTER_SECONDS": "not-a-number"}) == 0
    assert "REASSURANCE_AFTER_SECONDS" in WORKER_KEYS


def test_the_filler_is_stripped_from_the_persisted_turn(monkeypatch):
    async def main():
        persisted = []

        def backend(request):
            if request.url.path.endswith("/turn/start"):
                return httpx.Response(200, json=_snapshot())
            if request.url.path.endswith("/turn/finalize"):
                persisted.append(json.loads(request.content))
                return httpx.Response(200, json={})
            if request.url.path.endswith("/turn/metrics"):
                return httpx.Response(200, json={})
            raise AssertionError(request.url.path)

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")
            agent.prepared["m"] = {"turn_id": "turn"}
            agent.reassurance_fillers["turn"] = "One moment."
            message = llm.ChatMessage(role="assistant", content=["One moment. It is sunny."])
            handle = SimpleNamespace(
                id="h",
                interrupted=False,
                chat_items=[message],
                wait_for_playout=lambda: asyncio.sleep(0),
                exception=lambda: None,
            )
            agent._schedule_persistence(SimpleNamespace(id="m"), handle)
            await asyncio.gather(*agent.pending)
        assert persisted[0]["text"] == "It is sunny."

    asyncio.run(main())


def test_the_filler_is_stripped_from_the_model_continuation():
    chat_ctx = context()
    chat_ctx.add_message(role="assistant", content="One moment. It is sunny.")
    continuation = _continuation(chat_ctx, chat_ctx.items[0], "One moment.")
    assert continuation[0].content == "It is sunny."
    assert _without_leading_filler("One moment. It is sunny.", "Let me check that.") == ("One moment. It is sunny.")


def test_barge_in_after_the_filler_leaks_no_tasks(monkeypatch):
    async def main():
        worker = Worker(monkeypatch, {"command": None}, reassurance_seconds=0.05)

        async def scenario(agent, chat_ctx):
            await start_turn(agent, chat_ctx)
            worker.gate.set()

            async def never(_provider, _kind, _request, _tools, _tool_choice):
                await asyncio.Event().wait()
                yield text_event("never")

            agent._stream_attempt = never
            reply = asyncio.create_task(heard(agent, chat_ctx))
            await asyncio.sleep(0.1)  # the filler has been yielded; the real answer is pending
            reply.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reply

        await worker.run(scenario)

    asyncio.run(main())


def test_a_late_reply_keeps_its_contextvar_in_one_task():
    import asyncio
    from contextvars import ContextVar

    from reassurance import with_reassurance

    var: ContextVar[int | None] = ContextVar("var", default=None)

    async def reply():
        token = var.set(1)
        try:
            await asyncio.sleep(0.05)  # later than the threshold, so the filler wins the race
            yield "answer"
            yield "more"
        finally:
            var.reset(token)  # raises ValueError if another task resumed this generator

    async def run():
        timer = ReassuranceTimer(0.01)
        timer.begin("t", time.perf_counter())
        out = []
        async for item in with_reassurance(
            reply(), timer, "t", allowed=True, on_filler=lambda p: None, on_answer=lambda: None
        ):
            out.append(item if isinstance(item, str) else item.delta.content.strip())
        return out

    out = asyncio.run(run())
    assert out[1:] == ["answer", "more"] and out[0] in PHRASES


def test_app_spoken_tools_never_get_a_filler():
    from types import SimpleNamespace

    from reassurance import has_tool_call

    def chunk(name):
        return SimpleNamespace(delta=SimpleNamespace(tool_calls=[SimpleNamespace(name=name)]))

    assert not has_tool_call(chunk("propose_memory"))
    assert not has_tool_call(chunk("propose_forget"))
    assert has_tool_call(chunk("get_current_weather"))
