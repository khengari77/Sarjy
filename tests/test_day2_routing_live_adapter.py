import asyncio
import json
import math
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import day2_routing_benchmark as bench  # noqa: E402
import day2_routing_live_adapter as adapter  # noqa: E402
from app.domain.contracts import Intent  # noqa: E402
from app.domain.routing import JEV_MODEL, TIER_BY_OPTION  # noqa: E402
from app.services.action_extraction import GenerationConfig  # noqa: E402

FIXTURE = bench.load_fixture()
CASE = next(case for case in FIXTURE.cases if case.id == "tune-01")
GENERATION = GenerationConfig(
    model="deepseek-flash",
    max_output_tokens=32,
    input_usd_per_million=0.0,
    output_usd_per_million=0.0,
)


class AllowReservation:
    def __init__(self, allow=True):
        self.allow = allow
        self.calls = []

    async def __call__(self, request):
        self.calls.append(request)
        return self.allow


def intent_options():
    return [intent.value for intent in Intent]


def choice(names, selected, top=0.96):
    remainder = (1 - top) / (len(names) - 1)
    return {
        "type": "choice",
        "choice": selected,
        "confidence": 0.99,
        "probabilities": {name: (top if name == selected else remainder) for name in names},
    }


def classification(
    *,
    intent=Intent.CONVERSATION,
    tier=2,
    complexity="simple",
    top=0.96,
    intent_probabilities=None,
    model=JEV_MODEL,
):
    intent_answer = choice(intent_options(), intent.value, top=top)
    if intent_probabilities is not None:
        intent_answer["probabilities"] = intent_probabilities
    return {
        "model": model,
        "answers": {
            "intent": intent_answer,
            "response_tier": choice(
                list(TIER_BY_OPTION), next(name for name, value in TIER_BY_OPTION.items() if value == tier)
            ),
            "complexity": choice(["simple", "complex"], complexity),
        },
        "usage": {"input_tokens": 120, "output_tokens": 9},
    }


def jev_router(handler, *, reserve=None, deadline=0.75, extraction_handler=None):
    extraction_calls = []

    def default_extraction(_request):
        extraction_calls.append(_request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    if extraction_handler is not None:

        def wrapped_extraction(request):
            extraction_calls.append(request)
            return extraction_handler(request)

        extraction_transport = httpx.MockTransport(wrapped_extraction)
    else:
        extraction_transport = httpx.MockTransport(default_extraction)
    return (
        adapter.LiveJevRouter(
            FIXTURE,
            api_key="jev-test-key",
            deepseek_api_key="deepseek-test-key",
            reserve=reserve or AllowReservation(),
            generation=GENERATION,
            transport=httpx.MockTransport(handler),
            extraction_transport=extraction_transport,
            deadline_seconds=deadline,
        ),
        extraction_calls,
    )


def test_valid_response_records_usage_without_prompt_or_key():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=classification())

    router, _ = jev_router(handler)
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert output.intent == "conversation"
    assert output.tier == 2
    assert output.complexity == "simple"
    assert output.authorized is False
    assert output.action is None
    assert output.requests == 1
    assert output.usage == (
        bench.UsageRecord(
            provider="jev",
            model=JEV_MODEL,
            purpose="classification",
            input_tokens=120,
            output_tokens=9,
        ),
    )
    assert calls[0].headers["authorization"] == "Bearer jev-test-key"
    serialized = json.dumps(output.raw)
    assert CASE.text not in serialized
    assert "jev-test-key" not in serialized


def test_unknown_intent_is_a_recorded_safe_bypass():
    calls = []
    payload = classification()
    payload["answers"]["intent"]["choice"] = "not_an_intent"

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    router, _ = jev_router(handler)
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert output.error == "INVALID_RESPONSE"
    assert output.reason == "INVALID_RESPONSE"
    assert output.intent is None
    assert output.action is None
    assert output.authorized is False
    assert output.requests == 1


def test_malformed_payload_is_a_recorded_safe_bypass():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"model": JEV_MODEL})

    router, _ = jev_router(handler)
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert output.error == "INVALID_RESPONSE"
    assert output.authorized is False
    assert output.action is None


def test_low_confidence_never_authorizes_an_action():
    calls = []
    low_probabilities = {
        "conversation": 0.1,
        "ui_control": 0.1,
        "voice_setting": 0.4,
        "memory_search": 0.08,
        "memory_modification": 0.08,
        "conversation_navigation": 0.08,
        "tool_request": 0.08,
        "ambiguous": 0.08,
    }

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=classification(intent=Intent.VOICE_SETTING, intent_probabilities=low_probabilities),
        )

    router, extraction_calls = jev_router(handler)
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert extraction_calls == []
    assert output.reason == "LOW_CONFIDENCE"
    assert output.intent == "voice_setting"
    assert output.action is None
    assert output.authorized is False
    assert output.requests == 1


def test_conflicting_selected_answer_is_a_recorded_safe_bypass():
    calls = []
    probabilities = {
        "conversation": 0.1,
        "ui_control": 0.11,
        "voice_setting": 0.79,
        "memory_search": 0.0,
        "memory_modification": 0.0,
        "conversation_navigation": 0.0,
        "tool_request": 0.0,
        "ambiguous": 0.0,
    }

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=classification(intent=Intent.CONVERSATION, intent_probabilities=probabilities),
        )

    router, _ = jev_router(handler)
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert output.error == "INVALID_RESPONSE"
    assert output.authorized is False
    assert output.action is None


def test_deadline_produces_timeout_bypass_without_retry():
    calls = []

    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request)
            await asyncio.sleep(0.2)
            return httpx.Response(200, json=classification())

    router, _ = jev_router(lambda _request: httpx.Response(500), deadline=0.02)
    router._client._transport = SlowTransport()

    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert output.error == "TIMEOUT"
    assert output.reason == "TIMEOUT"
    assert output.authorized is False
    assert output.action is None


def test_exhausted_credit_is_a_recorded_safe_bypass():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(402, json={"error": {"message": "insufficient credit"}})

    router, _ = jev_router(handler)
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert output.error == "CREDIT_EXHAUSTED"
    assert output.authorized is False
    assert output.action is None


def test_reservation_denial_skips_without_a_network_call():
    calls = []
    budget = adapter.ReservationBudget(max_calls=0, max_tokens=1_000)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=classification())

    router, _ = jev_router(handler, reserve=budget)
    output = asyncio.run(router.route(CASE))

    assert calls == []
    assert budget.calls == 0
    assert budget.denials == ["CALL_CAP"]
    assert output.error == "RESERVATION_DENIED"
    assert output.reason == "RESERVATION_DENIED"
    assert output.authorized is False
    assert output.requests == 1  # one attempted Jev classification, zero provider calls


def test_token_cap_denies_without_a_network_call():
    calls = []
    budget = adapter.ReservationBudget(max_calls=10, max_tokens=0)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=classification())

    router, _ = jev_router(handler, reserve=budget)
    output = asyncio.run(router.route(CASE))

    assert calls == []
    assert budget.denials == ["TOKEN_CAP"]
    assert output.reason == "RESERVATION_DENIED"
    assert output.authorized is False


def test_invalid_extraction_is_never_authorized():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=classification(intent=Intent.VOICE_SETTING))

    extraction_calls = []

    def extraction(request):
        extraction_calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    router, recorded_extraction_calls = jev_router(handler, extraction_handler=extraction)
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert len(extraction_calls) == len(recorded_extraction_calls) == 1
    assert output.requests == 2
    assert output.action is None
    assert output.authorized is False
    assert output.reason == "INVALID_JSON"


def test_missing_keys_raise_configuration_errors(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    with pytest.raises(adapter.ConfigurationError, match="JEV_API_KEY"):
        adapter.build_routers(fixture=FIXTURE, cache=None)

    monkeypatch.setenv("JEV_API_KEY", "jev-test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(adapter.ConfigurationError, match="DEEPSEEK_API_KEY"):
        adapter.build_routers(fixture=FIXTURE, cache=None)


def test_missing_extraction_prices_raise_configuration_errors(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.delenv("SARJY_ROUTING_EXTRACTION_INPUT_PRICE", raising=False)
    monkeypatch.delenv("SARJY_ROUTING_EXTRACTION_OUTPUT_PRICE", raising=False)
    with pytest.raises(adapter.ConfigurationError, match="SARJY_ROUTING_EXTRACTION_INPUT_PRICE"):
        adapter.build_routers(fixture=FIXTURE, cache=None)

    monkeypatch.setenv("SARJY_ROUTING_EXTRACTION_INPUT_PRICE", "0.30")
    with pytest.raises(adapter.ConfigurationError, match="SARJY_ROUTING_EXTRACTION_OUTPUT_PRICE"):
        adapter.build_routers(fixture=FIXTURE, cache=None)

    monkeypatch.setenv("SARJY_ROUTING_EXTRACTION_OUTPUT_PRICE", "0")
    with pytest.raises(adapter.ConfigurationError, match="positive"):
        adapter.build_routers(fixture=FIXTURE, cache=None)


def test_build_routers_returns_live_routers_with_a_shared_budget(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("SARJY_ROUTING_EXTRACTION_INPUT_PRICE", "0.30")
    monkeypatch.setenv("SARJY_ROUTING_EXTRACTION_OUTPUT_PRICE", "1.20")
    monkeypatch.setenv("SARJY_ROUTING_LIVE_MAX_CALLS", "12")
    monkeypatch.setenv("SARJY_ROUTING_LIVE_MAX_TOKENS", "3456")

    routers = adapter.build_routers(fixture=FIXTURE, cache=None)

    assert [router.id for router in routers] == ["local_exact", "jev_assisted", "direct"]
    assert routers[1].provenance == routers[2].provenance == "live"
    assert routers[1].model == JEV_MODEL
    assert routers[2].model == adapter.DIRECT_MODEL
    budget = routers[1]._reserve
    assert budget is routers[2]._reserve
    assert budget.max_calls == 12
    assert budget.max_tokens == 3456
    assert routers[1].generation.input_usd_per_million == 0.30
    assert routers[1].generation.output_usd_per_million == 1.20
    assert routers[1].generation.input_usd_per_million > 0
    assert routers[1].generation.output_usd_per_million > 0


def test_direct_router_records_a_typed_action_and_usage():
    calls = []
    content = json.dumps(
        {
            "intent": "voice_setting",
            "tier": 1,
            "complexity": "simple",
            "action": {"kind": "SetSpeakingSpeed", "speed": 1.2},
        }
    )

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            },
        )

    reservations = AllowReservation()
    router = adapter.DirectLiveRouter(
        FIXTURE,
        api_key="deepseek-test-key",
        reserve=reservations,
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    prompt = adapter._direct_prompt(CASE, FIXTURE)
    estimated_input = max(1, math.ceil(len(prompt) / 4))
    expected_micro_usd = max(
        1,
        math.ceil(
            estimated_input * adapter.DEEPSEEK_PEAK_INPUT_MICRO_USD_PER_TOKEN
            + adapter.DIRECT_MAX_OUTPUT_TOKENS * adapter.DEEPSEEK_PEAK_OUTPUT_MICRO_USD_PER_TOKEN
        ),
    )
    assert reservations.calls[0].estimated_cost_micro_usd == expected_micro_usd
    assert reservations.calls[0].estimated_cost_micro_usd > math.ceil(estimated_input * 0.042)
    assert output.intent == "voice_setting"
    assert output.tier == 1
    assert output.complexity == "simple"
    assert output.action == {"kind": "SetSpeakingSpeed", "speed": 1.2}
    assert output.authorized is True
    assert output.requests == 1
    assert output.usage[0].input_tokens == 40
    assert output.usage[0].output_tokens == 8
    serialized = json.dumps(output.raw)
    assert CASE.text not in serialized
    assert "deepseek-test-key" not in serialized


@pytest.mark.parametrize(
    ("tier_value", "expected"),
    [
        (1, 1),
        ("tier_1_short_acknowledgment", 1),
        ("tier_2_normal_conversation", 2),
        ("tier_3_moderate_explanation", 3),
        ("tier_4_detailed_explanation", 4),
        ("tier_5_explicitly_detailed", 5),
    ],
)
def test_direct_router_accepts_tier_numbers_and_documented_labels(tier_value, expected):
    content = json.dumps({"intent": "conversation", "tier": tier_value, "complexity": "simple", "action": None})

    def handler(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    router = adapter.DirectLiveRouter(
        FIXTURE,
        api_key="deepseek-test-key",
        reserve=AllowReservation(),
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(router.route(CASE))

    assert output.intent == "conversation"
    assert output.tier == expected
    assert output.complexity == "simple"
    assert output.authorized is False
    assert output.error is None
    assert output.raw["tier"] == tier_value
    assert output.raw["tier_number"] == expected


def test_direct_router_rejects_an_unknown_tier_string():
    content = json.dumps({"intent": "conversation", "tier": "tier_9_unknown", "complexity": "simple", "action": None})

    def handler(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    router = adapter.DirectLiveRouter(
        FIXTURE,
        api_key="deepseek-test-key",
        reserve=AllowReservation(),
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(router.route(CASE))

    assert output.tier is None
    assert output.intent is None
    assert output.authorized is False
    assert output.error == "INVALID_DIRECT_PAYLOAD"


def test_direct_router_malformed_content_is_a_safe_failure():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    reservations = AllowReservation()
    router = adapter.DirectLiveRouter(
        FIXTURE,
        api_key="deepseek-test-key",
        reserve=reservations,
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(router.route(CASE))

    assert len(calls) == 1
    assert output.error == "INVALID_RESPONSE"
    assert output.authorized is False
    assert output.action is None


def test_direct_router_respects_the_shared_budget():
    calls = []
    budget = adapter.ReservationBudget(max_calls=0, max_tokens=1_000)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    router = adapter.DirectLiveRouter(
        FIXTURE,
        api_key="deepseek-test-key",
        reserve=budget,
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(router.route(CASE))

    assert calls == []
    assert output.error == "RESERVATION_DENIED"
    assert output.requests == 0
    assert output.authorized is False


def test_report_keeps_automatic_routing_disabled():
    report = bench.build_report(FIXTURE, [], mode="live")
    assert report["automatic_routing_enabled"] is False
