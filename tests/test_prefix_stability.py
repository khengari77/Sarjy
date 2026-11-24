"""Prefix stability estimates DeepSeek prompt-cache hits from the prior request."""

import json
import time

from prefix_stability import canonical_prompt, common_prefix_chars, predicted_prefix_hit_pct


def test_identical_prompt_is_a_full_hit():
    prompt = "x" * 1024
    assert common_prefix_chars(prompt, prompt) == 1024
    assert predicted_prefix_hit_pct(prompt, prompt) == 100


def test_appended_tail_keeps_almost_all_of_the_prefix():
    prompt = "x" * 1024
    current = prompt + "tail"
    assert common_prefix_chars(prompt, current) == 1024
    assert predicted_prefix_hit_pct(prompt, current) == 99


def test_changed_first_message_has_no_hit():
    previous = "alpha\n" + "x" * 4096
    current = "beta\n" + "x" * 4096
    assert common_prefix_chars(previous, current) == 0
    assert predicted_prefix_hit_pct(previous, current) == 0


def test_no_previous_request_or_empty_current_is_unmeasured():
    assert predicted_prefix_hit_pct(None, "anything") is None
    assert predicted_prefix_hit_pct("anything", "") is None


def test_common_prefix_is_aligned_down_to_a_cache_block():
    previous = "x" * 500
    current = previous + "y" * 500
    assert common_prefix_chars(previous, current) == 500
    assert predicted_prefix_hit_pct(previous, current) == 256 * 100 // 1000


def test_a_large_prompt_is_compared_without_a_character_loop():
    previous = "x" * 300_000
    current = previous[:-1] + "y"
    started = time.perf_counter()
    assert common_prefix_chars(previous, current) == 299_999
    assert time.perf_counter() - started < 0.05


def test_canonical_prompt_serializes_tools_and_messages_in_order():
    """Uses a real ModelRequest: only its messages, not selection or memories, enter the canonical string."""
    from app.domain.context import ContextCandidate, compile_turn_context
    from app.domain.contracts import ModelRequest, ResponsePolicy

    context = compile_turn_context(
        revision=1,
        instructions="Rules",
        history=(),
        memories=(ContextCandidate(id="m1", role="memory", text="A fact", origin="memory"),),
        tool_results=(),
        current_turn=ContextCandidate(id="u1", role="user", text="Hi", origin="history"),
    )
    request = ModelRequest(
        turn_id="turn",
        attempt_id="attempt",
        instructions="Rules",
        selection=context.selection,
        messages=context.messages,
        response_policy=ResponsePolicy.for_tier(2),
        settings=None,
        settings_revision=None,
        language="en",
    )
    tools = [{"name": "weather", "description": "Look up weather", "parameters": {"type": "object"}}]
    canonical = canonical_prompt(request, tools)
    assert json.loads(canonical) == {"tools": tools, "messages": request.model_dump(mode="json")["messages"]}
    assert list(json.loads(canonical)) == ["tools", "messages"]
    assert "selection" not in canonical
