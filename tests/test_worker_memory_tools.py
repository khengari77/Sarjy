"""Worker state for the typed memory tools, driven by scripted model tool calls.

No live provider: a scripted "model" maps an utterance to one tool call, the
test invokes that tool exactly as the SDK would, then runs the continuation
``llm_node`` pass.  The backend is an in-memory ``httpx.MockTransport``.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from livekit.agents import llm
from main import SarjyAgent
from test_worker_commands import _snapshot

PROPOSAL = {
    "token": "signed-token",
    "text": "I live in Benghazi",
    "confirmation_id": "c1",
    "confirmation_revision": 1,
    "question": "Should I remember this: I live in Benghazi?",
}
FORGET_SPOKEN = "Exclude 'I prefer green tea' from this conversation? Say yes to exclude, or no."


class CountingProvider:
    def __init__(self):
        self.calls = 0

    @asynccontextmanager
    async def chat(self, **_kwargs):
        self.calls += 1

        async def chunks():
            yield llm.ChatChunk(id="r", delta=llm.ChoiceDelta(content="model prose"))

        yield chunks()


class Handle:
    id = "handle"
    interrupted = False
    chat_items: list = []

    def __init__(self, ident):
        self.id = ident

    async def wait_for_playout(self):
        return None

    def exception(self):
        return None


class Harness:
    def __init__(self, monkeypatch, *, tool_status=200, tool_bodies=None, regex_proposals=None, play=True):
        self.provider = CountingProvider()
        self.paths: list[str] = []
        self.confirms: list[dict] = []
        self.tool_bodies = tool_bodies or {}
        self.tool_status = tool_status
        self.regex_proposals = regex_proposals or {}
        self.turns: dict[str, str] = {}
        self.play = play
        self.handle: Handle | None = None
        self.n = 0
        monkeypatch.setattr(
            SarjyAgent,
            "session",
            property(lambda agent: SimpleNamespace(current_speech=self.handle, llm=self.provider)),
        )

    def backend(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/turn/start"):
            snap = _snapshot()
            snap["turn_id"] = f"turn-{body['request_id']}"
            snap["context"]["model_request"]["turn_id"] = snap["turn_id"]
            snap["memory_proposal"] = self.regex_proposals.get(body["text"])
            return httpx.Response(200, json=snap)
        if "/internal/agent/tool/" in path:
            name = path.rsplit("/", 1)[1]
            return httpx.Response(self.tool_status, json=self.tool_bodies.get(name, {}))
        if path.endswith("/memory/confirm"):
            self.confirms.append(body)
            return httpx.Response(200, json={"status": "saved"})
        if path.endswith("/internal/agent/command"):
            return httpx.Response(200, json={"command": None})
        return httpx.Response(200, json={})

    async def run(self, steps):
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(self.backend)) as client:
            agent = SarjyAgent(client, {"llm": {"provider": "openai_direct"}, "max_output_tokens": 128}, "Rules")
            self.agent = agent
            return await steps(self, agent)

    async def say(self, agent, text, tool=None, **arguments):
        """One user turn: optional scripted tool call, then the continuation pass."""
        self.n += 1
        message = llm.ChatMessage(id=f"m{self.n}", role="user", content=[text])
        ctx = llm.ChatContext()
        ctx.items.append(message)
        await agent.on_user_turn_completed(ctx, message)
        result = None
        if tool is not None:
            call = SimpleNamespace(function_call=SimpleNamespace(call_id=f"call-{self.n}"))
            result = await getattr(agent, tool)._func(agent, call, **arguments)
        self.handle = Handle(f"h{self.n}")
        chunks = [chunk.delta.content async for chunk in agent.llm_node(ctx, [], None) if chunk.delta]
        if agent.pending:
            await asyncio.gather(*agent.pending)
        return result, chunks


def run(monkeypatch, steps, **kwargs):
    harness = Harness(monkeypatch, **kwargs)
    asyncio.run(harness.run(steps))
    return harness


REMEMBER_BODY = {
    "memory-propose": {"status": "proposed", "proposal": PROPOSAL, "message": "Nothing has been saved yet."}
}
FORGET_BODY = {
    "memory-forget": {
        "status": "proposed",
        "armed": True,
        "spoken": FORGET_SPOKEN,
        "message": "Nothing has been excluded.",
    }
}

REMEMBER_PARAPHRASES = [
    "I've moved to Benghazi",
    "Just so you know, Benghazi is home now",
    "Keep in mind that I live in Benghazi",
    "Make a note: my city is Benghazi",
]
FORGET_PARAPHRASES = ["Scrap what I said about tea", "Drop the tea thing", "Pretend I never mentioned tea"]


@pytest.mark.parametrize("utterance", REMEMBER_PARAPHRASES)
def test_remember_paraphrase_speaks_the_deterministic_question_and_arms_after_playout(monkeypatch, utterance):
    async def steps(h, agent):
        result, chunks = await h.say(agent, utterance, "propose_memory", fact="I live in Benghazi")
        assert "Nothing has been saved" in result
        assert chunks == [PROPOSAL["question"]]
        assert agent.memory_prompted_key == ("c1", 1)
        await h.say(agent, "yes")
        assert [c["decision"] for c in h.confirms] == ["save"]
        # Consent is one-shot: a second yes finds nothing armed.
        await h.say(agent, "yes")
        assert len(h.confirms) == 1

    run(monkeypatch, steps, tool_bodies=REMEMBER_BODY)


def test_model_never_receives_a_save_before_confirmation(monkeypatch):
    async def steps(h, agent):
        await h.say(agent, "I've moved to Benghazi", "propose_memory", fact="I live in Benghazi")
        assert h.confirms == []
        assert agent.memory_proposal == PROPOSAL

    run(monkeypatch, steps, tool_bodies=REMEMBER_BODY)


def test_consent_is_not_armed_when_the_question_never_finished_playing(monkeypatch):
    async def steps(h, agent):
        h.n += 1
        message = llm.ChatMessage(id="m1", role="user", content=["I've moved to Benghazi"])
        ctx = llm.ChatContext()
        ctx.items.append(message)
        await agent.on_user_turn_completed(ctx, message)
        call = SimpleNamespace(function_call=SimpleNamespace(call_id="call-1"))
        await agent.propose_memory._func(agent, call, fact="I live in Benghazi")
        h.handle = None  # no speech handle: the question was never played
        [chunk async for chunk in agent.llm_node(ctx, [], None)]
        assert agent.memory_prompted_key is None
        await h.say(agent, "yes")
        assert h.confirms == []

    run(monkeypatch, steps, tool_bodies=REMEMBER_BODY)


@pytest.mark.parametrize("utterance", FORGET_PARAPHRASES)
def test_forget_paraphrase_speaks_the_exact_question_without_model_prose(monkeypatch, utterance):
    async def steps(h, agent):
        result, chunks = await h.say(agent, utterance, "propose_forget", query="tea")
        assert "Nothing has been excluded" in result
        assert chunks == [FORGET_SPOKEN]
        assert agent.memory_proposal is None
        assert h.provider.calls == 0
        before = len(h.confirms)
        await h.say(agent, "yes")
        # The yes belongs to the exclusion flow (backend command), never memory/confirm.
        assert len(h.confirms) == before
        assert "/internal/agent/command" in h.paths

    run(monkeypatch, steps, tool_bodies=FORGET_BODY)


def test_forget_with_no_match_speaks_the_refusal_and_arms_nothing(monkeypatch):
    spoken = "I couldn't find anything matching skydiving; nothing was changed."
    body = {
        "memory-forget": {"status": "not_proposed", "armed": False, "spoken": spoken, "message": "Nothing changed."}
    }

    async def steps(h, agent):
        _, chunks = await h.say(agent, "Forget skydiving", "propose_forget", query="skydiving")
        assert chunks == [spoken]

    run(monkeypatch, steps, tool_bodies=body)


def test_forget_after_a_pending_remember_disarms_it_so_both_are_never_armed(monkeypatch):
    async def steps(h, agent):
        await h.say(agent, "I've moved to Benghazi", "propose_memory", fact="I live in Benghazi")
        assert agent.memory_proposal is not None
        await h.say(agent, "Actually forget the tea thing", "propose_forget", query="tea")
        assert agent.memory_proposal is None and agent.memory_prompted_key is None
        await h.say(agent, "yes")
        assert h.confirms == []

    run(monkeypatch, steps, tool_bodies={**REMEMBER_BODY, **FORGET_BODY})


def test_remember_after_a_pending_forget_leaves_only_the_remember_armed(monkeypatch):
    async def steps(h, agent):
        await h.say(agent, "forget tea", "propose_forget", query="tea")
        _, chunks = await h.say(agent, "I've moved to Benghazi", "propose_memory", fact="I live in Benghazi")
        assert chunks == [PROPOSAL["question"]]
        await h.say(agent, "yes")
        # The worker consent path owns this yes; the exclusion command is blocked for the turn.
        assert [c["decision"] for c in h.confirms] == ["save"]
        assert h.paths.count("/internal/agent/command") == 2  # the two tool turns only

    run(monkeypatch, steps, tool_bodies={**REMEMBER_BODY, **FORGET_BODY})


def test_stray_yes_after_an_unrelated_turn_changes_nothing(monkeypatch):
    async def steps(h, agent):
        await h.say(agent, "I've moved to Benghazi", "propose_memory", fact="I live in Benghazi")
        await h.say(agent, "What's two plus two?")
        assert agent.memory_proposal is None
        await h.say(agent, "yes")
        assert h.confirms == []

    run(monkeypatch, steps, tool_bodies=REMEMBER_BODY)


def test_tool_http_failure_never_claims_success_or_arms(monkeypatch):
    async def steps(h, agent):
        for tool, args in (("propose_memory", {"fact": "I live in Benghazi"}), ("propose_forget", {"query": "tea"})):
            result, _ = await h.say(agent, "x", tool, **args)
            assert "Nothing was saved, excluded or changed" in result
            assert agent.memory_proposal is None and agent.forced_speech is None
        result, _ = await h.say(agent, "what do you know", "recall", query="me")
        assert json.loads(result)["error"] == "MEMORY_TOOL_FAILED"

    run(monkeypatch, steps, tool_status=503)


def test_rejected_fact_does_not_arm_anything(monkeypatch):
    body = {
        "memory-propose": {
            "status": "rejected",
            "error": "MEMORY_FACT_NOT_REMEMBERABLE",
            "message": "Do not claim it was saved.",
        }
    }

    async def steps(h, agent):
        result, _ = await h.say(agent, "it", "propose_memory", fact="it")
        assert result == "Do not claim it was saved."
        assert agent.memory_proposal is None

    run(monkeypatch, steps, tool_bodies=body)


def test_recall_returns_data_to_the_tool_role_and_changes_no_state(monkeypatch):
    body = {"memory-recall": {"status": "ok", "memories": [{"content": "I work at Acme"}], "message": "data only"}}

    async def steps(h, agent):
        result, _ = await h.say(agent, "What do you know about my job?", "recall", query="job")
        assert json.loads(result)["memories"] == [{"content": "I work at Acme"}]
        assert agent.memory_proposal is None and agent.forced_speech is None
        assert "Acme" not in agent.instructions

    run(monkeypatch, steps, tool_bodies=body)


def test_regex_fast_path_needs_no_tool_and_no_model_call(monkeypatch):
    async def steps(h, agent):
        _, chunks = await h.say(agent, "Remember that I live in Benghazi")
        assert chunks == [PROPOSAL["question"]]

    h = run(monkeypatch, steps, regex_proposals={"Remember that I live in Benghazi": PROPOSAL})
    assert h.provider.calls == 0
    assert not any("/internal/agent/tool/" in path for path in h.paths)


def test_a_tool_armed_turn_is_not_rearmed_from_the_turn_start_proposal(monkeypatch):
    stale = {**PROPOSAL, "confirmation_id": "regex", "token": "regex-token"}

    async def steps(h, agent):
        await h.say(agent, "tea stuff", "propose_forget", query="tea")
        assert agent.memory_proposal is None

    run(monkeypatch, steps, tool_bodies=FORGET_BODY, regex_proposals={"tea stuff": stale})


SAVED_BODY = {
    "memory-propose": {"status": "rejected", "error": "MEMORY_ALREADY_SAVED", "message": "That is already saved."}
}


def test_the_confirmation_turn_cannot_start_a_new_proposal(monkeypatch):
    async def steps(h, agent):
        await h.say(agent, "I've moved to Benghazi", "propose_memory", fact="I live in Benghazi")
        posts = len([p for p in h.paths if p.endswith("memory-propose")])
        # The model re-proposes on the very turn that answers the confirmation.
        result, _ = await h.say(agent, "yes", "propose_memory", fact="I live in Benghazi")
        assert [c["decision"] for c in h.confirms] == ["save"]
        assert "answering the previous confirmation" in result
        assert len([p for p in h.paths if p.endswith("memory-propose")]) == posts
        assert agent.memory_proposal is None
        result, _ = await h.say(agent, "yes", "propose_forget", query="tea")
        assert agent.forced_speech is None and agent.memory_proposal is None

    run(monkeypatch, steps, tool_bodies=REMEMBER_BODY)


def test_an_unrelated_turn_repeat_proposal_is_rejected_and_arms_nothing(monkeypatch):
    async def steps(h, agent):
        await h.say(agent, "I've moved to Benghazi", "propose_memory", fact="I live in Benghazi")
        await h.say(agent, "yes")
        h.tool_bodies = SAVED_BODY
        _, chunks = await h.say(agent, "mumble garbled", "propose_memory", fact="I live in Benghazi")
        assert agent.memory_proposal is None and agent.forced_speech is None
        assert PROPOSAL["question"] not in chunks
        await h.say(agent, "yes")
        assert len(h.confirms) == 1

    run(monkeypatch, steps, tool_bodies=REMEMBER_BODY)
