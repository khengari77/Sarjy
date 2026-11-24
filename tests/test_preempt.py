"""Early (preemptive) replies: adoption, abandonment and the state they must not touch."""

import asyncio
from types import SimpleNamespace

import httpx
from endpointing import endpointing_kwargs
from main import SarjyAgent
from preempt import RecentFinals, transcripts_equivalent


def _agent(client, *, preemptive=True):
    return SarjyAgent(
        client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules", preemptive=preemptive
    )


def _run(body):
    calls: list[tuple[str, dict]] = []

    def backend(request):
        import json

        payload = json.loads(request.content or b"{}")
        calls.append((request.url.path, payload))
        if request.url.path.endswith("/turn/start"):
            return httpx.Response(200, json={"turn_id": f"turn-{payload['request_id']}", "context": {}})
        return httpx.Response(200, json={})

    async def main():
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(backend)) as client:
            agent = _agent(client)
            await body(agent)
            await asyncio.gather(*agent.pending)

    asyncio.run(main())
    return calls


def _starts(calls):
    return [payload["request_id"] for path, payload in calls if path.endswith("/turn/start")]


def test_equivalence_ignores_case_punctuation_and_spacing():
    assert transcripts_equivalent("What's the weather?", "whats  the weather")
    assert not transcripts_equivalent("what is the weather", "what is the weather in Tripoli")


def test_a_final_segment_marks_spoken_input_and_typed_input_does_not_match():
    finals = RecentFinals()
    assert not finals.covers("hello")
    finals.add("Hello there.")
    assert finals.covers("hello there") and finals.covers("well, hello there")
    assert not finals.covers("something typed")
    finals.clear()
    assert not finals.covers("hello there")


def test_an_adopted_early_reply_opens_one_turn_only():
    async def body(agent):
        early = SimpleNamespace(id="early", text_content="What time is it?")
        entry = await agent._open_early_turn(early)
        assert entry.mode == "early" and entry.turn_id == "turn-early"
        final = SimpleNamespace(id="final", text_content="what time is it")
        await agent.on_user_turn_completed(None, final)
        assert entry.adopted and agent.preempt is None
        assert "early" in agent.prepared and "final" not in agent.prepared

    calls = _run(body)
    assert _starts(calls) == ["early"]
    assert not [path for path, _ in calls if path.endswith("/turn/finalize")]


def test_a_changed_transcript_closes_the_early_turn_once_and_opens_the_real_one():
    async def body(agent):
        await agent._open_early_turn(SimpleNamespace(id="early", text_content="What time"))
        await agent.on_user_turn_completed(None, SimpleNamespace(id="final", text_content="What time is it in Rome"))
        assert agent.preempt is None and "final" in agent.prepared
        agent._retire_preempt()  # a second retirement is a no-op

    calls = _run(body)
    assert _starts(calls) == ["early", "final"]
    finalized = [payload for path, payload in calls if path.endswith("/turn/finalize")]
    assert len(finalized) == 1 and finalized[0]["status"] == "failed" and finalized[0]["turn_id"] == "turn-early"


def test_a_newer_early_reply_retires_the_one_before_it():
    async def body(agent):
        await agent._open_early_turn(SimpleNamespace(id="first", text_content="hello"))
        await agent._open_early_turn(SimpleNamespace(id="second", text_content="hello again"))

    calls = _run(body)
    assert _starts(calls) == ["first", "second"]
    assert [p["turn_id"] for path, p in calls if path.endswith("/turn/finalize")] == ["turn-first"]


def test_a_pending_memory_question_waits_for_the_hook_instead_of_opening_a_turn():
    async def body(agent):
        agent.memory_proposal = {"token": "t", "text": "likes tea"}
        entry = await agent._open_early_turn(SimpleNamespace(id="early", text_content="yes"))
        assert entry.mode == "wait" and entry.turn_id is None
        await agent.on_user_turn_completed(None, SimpleNamespace(id="final", text_content="yes"))
        assert agent.preempt is None and "final" in agent.prepared and not entry.adopted

    calls = _run(body)
    assert _starts(calls) == ["final"]


def test_a_command_utterance_never_opens_an_early_turn():
    async def body(agent):
        agent.is_command = lambda text: text == "reveal"
        entry = await agent._open_early_turn(SimpleNamespace(id="early", text_content="reveal"))
        assert entry.mode == "wait"

    assert _starts(_run(body)) == []


def test_the_flag_chooses_the_session_preemption_options():
    assert endpointing_kwargs({})["turn_handling"]["preemptive_generation"] == {"enabled": False}
    on = endpointing_kwargs({"PREEMPTIVE_GENERATION": "1"})["turn_handling"]["preemptive_generation"]
    assert on["enabled"] is True and on["preemptive_tts"] is False and on["max_retries"] == 1


def test_the_sdk_still_exposes_the_cancel_the_worker_relies_on():
    from livekit.agents.voice.agent_activity import AgentActivity

    assert callable(getattr(AgentActivity, "_cancel_preemptive_generation", None))


def test_a_retired_turn_is_never_persisted_or_finalized_a_second_time():
    async def body(agent):
        entry = await agent._open_early_turn(SimpleNamespace(id="early", text_content="hello"))
        agent._retire_preempt()
        handle = SimpleNamespace(id="speech-1")
        agent._schedule_persistence(SimpleNamespace(id="early"), handle)  # a late chunk of the cancelled reply
        assert entry.retired and "speech-1" not in agent.speeches

    calls = _run(body)
    assert len([1 for path, _ in calls if path.endswith("/turn/finalize")]) == 1


def test_an_early_turn_whose_playout_already_started_is_left_to_that_playout():
    async def body(agent):
        entry = await agent._open_early_turn(SimpleNamespace(id="early", text_content="hello"))
        agent.persisting.add(entry.turn_id)
        agent._retire_preempt()

    assert not [1 for path, _ in _run(body) if path.endswith("/turn/finalize")]


def test_an_early_turn_retired_while_it_is_still_opening_is_finalized_once_it_opens():
    async def body(agent):
        opening = asyncio.create_task(agent._open_early_turn(SimpleNamespace(id="early", text_content="hello")))
        await asyncio.sleep(0)
        agent._retire_preempt()  # the entry is not even registered with a turn id yet
        await opening

    calls = _run(body)
    assert [p["turn_id"] for path, p in calls if path.endswith("/turn/finalize")] == ["turn-early"]


def test_a_retired_early_turn_restores_the_memory_state_its_compile_armed():
    async def body(agent):
        assert agent.memory_proposal is None
        entry = await agent._open_early_turn(SimpleNamespace(id="early", text_content="I live in Benghazi"))
        agent.memory_proposal = {"token": "interim", "text": "lives in Benghazi"}  # what compile would arm
        agent.memory_outcome = "not_saved"
        agent._retire_preempt()
        assert agent.memory_proposal is None and agent.memory_outcome is None and entry.retired

    _run(body)


def test_an_early_reply_waits_to_be_adopted_before_it_may_run_a_command():
    async def body(agent):
        entry = await agent._open_early_turn(SimpleNamespace(id="early", text_content="slow down"))
        assert not entry.decided.done()
        assert await agent._adopt_preempt("slow down") is True and entry.decided.result() is True

    _run(body)


def test_a_retired_entry_is_never_adopted_and_its_gate_closes():
    async def body(agent):
        entry = await agent._open_early_turn(SimpleNamespace(id="early", text_content="hello"))
        agent._retire_preempt()
        assert entry.decided.result() is False
        agent.preempt = entry
        assert await agent._adopt_preempt("hello") is False

    _run(body)


def test_empty_transcripts_are_never_equivalent():
    assert not transcripts_equivalent("", "")


def test_a_cancelled_early_open_does_not_cancel_a_hook_waiting_for_it():
    async def body(agent):
        gate = asyncio.Event()
        original = agent.compile

        async def slow(message):
            await gate.wait()
            return await original(message)

        agent.compile = slow
        opening = asyncio.create_task(agent._open_early_turn(SimpleNamespace(id="early", text_content="hello")))
        await asyncio.sleep(0)
        hook = asyncio.create_task(agent._adopt_preempt("hello"))
        await asyncio.sleep(0)
        opening.cancel()
        assert await hook is False  # the waiting hook falls back to its own turn, uncancelled

    _run(body)


def test_a_turn_opened_after_its_open_was_cancelled_is_still_closed_and_leaves_state_alone():
    async def body(agent):
        gate = asyncio.Event()
        original = agent.compile

        async def slow(message, live=None):
            await gate.wait()
            return await original(message, live=live)

        agent.compile = slow
        agent.current_turn = "live-turn"
        opening = asyncio.create_task(agent._open_early_turn(SimpleNamespace(id="early", text_content="hello")))
        await asyncio.sleep(0)
        agent._cancel = opening.cancel()
        agent._retire_preempt()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await opening
        gate.set()
        await asyncio.sleep(0.05)
        assert agent.current_turn == "live-turn"  # the late compile did not clobber it

    calls = _run(body)
    assert [p["turn_id"] for path, p in calls if path.endswith("/turn/finalize")] == ["turn-early"]
