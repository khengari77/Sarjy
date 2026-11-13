import json
import math
from pathlib import Path

import pytest
from app.domain.contracts import (
    ACTION_TYPES,
    TIER_CAPS,
    AdjustSpeakingSpeed,
    Complexity,
    ContextEntry,
    ContextSelection,
    Decision,
    DecisionSource,
    Intent,
    MemoryRecord,
    MemorySource,
    ModelRequest,
    OpenMemoryExplorer,
    ResponsePreference,
    SearchMemory,
    SelectApprovedVoice,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    ShowConversationHistory,
    ToolSpec,
    UserSettings,
    apply_settings_action,
    contract_components,
    parse_action,
    resolve_response_policy,
)
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
REALISTIC_CONFIDENCE = 0.9428571428571428


def probabilities(chosen=Intent.CONVERSATION, value=0.95):
    rest = (1 - value) / (len(Intent) - 1)
    return {intent: (value if intent is chosen else rest) for intent in Intent}


def decision(**overrides):
    values = {
        "turn_id": "turn-1",
        "intent": Intent.CONVERSATION,
        "probabilities": probabilities(),
        "confidence": 0.95,
        "tier": 2,
        "complexity": Complexity.SIMPLE,
        "source": DecisionSource.JEV,
        "model": "jev-1.14.0",
        "policy_version": "v1",
    }
    values.update(overrides)
    return Decision(**values)


def memory(identifier="memory-1", **overrides):
    values = {
        "id": identifier,
        "owner_id": "owner-1",
        "content": "The user lives in Tripoli.",
        "source": MemorySource(
            turn_id="turn-1", conversation_id="conversation-1", created_at="2026-11-11T00:00:00+00:00"
        ),
        "version": 1,
    }
    values.update(overrides)
    return MemoryRecord(**values)


def test_tier_caps_are_exact_and_invalid_tiers_are_rejected():
    assert TIER_CAPS == (64, 128, 256, 512, 1024)
    for tier, cap in enumerate(TIER_CAPS, start=1):
        assert resolve_response_policy(explicit_tier=tier).max_output_tokens == cap
    with pytest.raises(ValidationError):
        resolve_response_policy(explicit_tier=0)
    with pytest.raises(ValidationError):
        resolve_response_policy(explicit_tier=6)
    # Explicit current-turn instructions win, even outside a saved preference cap.
    assert resolve_response_policy(ResponsePreference.BRIEF, explicit_tier=5).tier == 5
    assert resolve_response_policy(ResponsePreference.BRIEF, explicit_tier=5).max_output_tokens == 1024
    assert resolve_response_policy(ResponsePreference.BALANCED, explicit_tier=3).tier == 3


@pytest.mark.parametrize("preference", list(ResponsePreference))
@pytest.mark.parametrize("tier", range(1, 6))
def test_explicit_tier_overrides_saved_preference_for_every_tier(preference, tier):
    policy = resolve_response_policy(preference, explicit_tier=tier)
    assert policy.tier == tier
    assert policy.max_output_tokens == TIER_CAPS[tier - 1]


@pytest.mark.parametrize(
    ("preference", "classified_tier", "expected"),
    [
        (ResponsePreference.AUTOMATIC, 1, 1),
        (ResponsePreference.AUTOMATIC, 5, 5),
        (ResponsePreference.BRIEF, 1, 1),
        (ResponsePreference.BRIEF, 3, 2),
        (ResponsePreference.BRIEF, 5, 2),
        (ResponsePreference.BALANCED, 2, 2),
        (ResponsePreference.BALANCED, 4, 3),
        (ResponsePreference.BALANCED, 5, 3),
        (ResponsePreference.DETAILED, 4, 4),
        (ResponsePreference.DETAILED, 5, 5),
    ],
)
def test_classified_tier_is_bounded_by_saved_preference(preference, classified_tier, expected):
    policy = resolve_response_policy(preference, classified_tier=classified_tier)
    assert policy.tier == expected
    assert policy.max_output_tokens == TIER_CAPS[expected - 1]


@pytest.mark.parametrize("preference", list(ResponsePreference))
def test_default_policy_is_normal_conversation_tier(preference):
    policy = resolve_response_policy(preference)
    assert policy.tier == 2
    assert policy.max_output_tokens == TIER_CAPS[1]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"explicit_tier": 0},
        {"explicit_tier": 6},
        {"classified_tier": 0},
        {"classified_tier": 6},
    ],
)
def test_invalid_explicit_and_classified_tiers_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        resolve_response_policy(**kwargs)


def test_unknown_actions_and_invalid_arguments_are_rejected():
    with pytest.raises(ValidationError):
        parse_action({"kind": "DeleteEverything"})
    with pytest.raises(ValidationError):
        parse_action({"kind": "SetCaptionsVisible", "visible": True, "extra": 1})
    with pytest.raises(ValidationError):
        AdjustSpeakingSpeed(delta=0.05)
    with pytest.raises(ValidationError):
        AdjustSpeakingSpeed(delta=0)
    with pytest.raises(ValidationError):
        SetSpeakingSpeed(speed=1.25)
    with pytest.raises(ValidationError):
        UserSettings(speaking_speed=1.05)
    with pytest.raises(ValidationError):
        SelectApprovedVoice(voice_id="bad voice!")


def test_every_shared_action_round_trips_through_the_discriminator():
    payloads = [
        {"kind": "AdjustSpeakingSpeed", "delta": -0.1},
        {"kind": "SetSpeakingSpeed", "speed": 1.2},
        {"kind": "SelectApprovedVoice", "voice_id": "voice-1"},
        {"kind": "ShowConversationHistory"},
        {"kind": "OpenMemoryExplorer"},
        {"kind": "SetCaptionsVisible", "visible": False},
        {"kind": "SearchMemory", "query": "trip", "limit": 5},
        {"kind": "ProposeContextExclusion", "query": "trip", "keep": ["budget"]},
        {"kind": "RestoreContextOperation", "operation_id": "operation-1"},
    ]
    assert {type(parse_action(payload)) for payload in payloads} == set(ACTION_TYPES)


def test_vendor_confidence_is_independent_of_selected_probability():
    decision_value = decision(confidence=REALISTIC_CONFIDENCE)
    assert decision_value.selected_probability == pytest.approx(0.95)
    assert decision_value.confidence == pytest.approx(REALISTIC_CONFIDENCE)
    assert decision_value.permits_automatic_action()
    # Confidence is informational; it must not drive either routing check.
    assert decision(confidence=0.1).permits_automatic_action()
    assert not decision(confidence=0.99, probabilities=probabilities(value=0.6)).permits_automatic_action()


def test_selected_intent_must_be_the_maximum_probability_outcome():
    outcomes = {intent: 0.025 for intent in Intent}
    outcomes[Intent.CONVERSATION] = 0.4
    outcomes[Intent.AMBIGUOUS] = 0.45
    with pytest.raises(ValidationError):
        decision(intent=Intent.CONVERSATION, probabilities=outcomes, confidence=0.95)


def test_routing_uses_selected_probability_and_runner_up_margin():
    tied = {intent: 0.125 for intent in Intent}
    tied_decision = decision(probabilities=tied, confidence=0.99)
    assert tied_decision.lead == pytest.approx(0.0)
    assert not tied_decision.permits_automatic_action()

    ambiguous = decision(intent=Intent.AMBIGUOUS, probabilities=probabilities(Intent.AMBIGUOUS), confidence=0.99)
    assert not ambiguous.permits_automatic_action()

    assert decision().permits_automatic_action(threshold=0.95, lead=0.2)
    assert not decision().permits_automatic_action(threshold=0.9500001, lead=0.2)

    outcomes = {intent: (1 - 0.5 - 0.3) / (len(Intent) - 2) for intent in Intent}
    outcomes[Intent.CONVERSATION] = 0.5
    outcomes[Intent.AMBIGUOUS] = 0.3
    margin_decision = decision(probabilities=outcomes, confidence=0.1)
    assert margin_decision.lead == pytest.approx(0.2)
    assert margin_decision.permits_automatic_action(threshold=0.4, lead=0.2)
    assert not margin_decision.permits_automatic_action(threshold=0.4, lead=0.2000001)
    assert not margin_decision.permits_automatic_action(threshold=0.6, lead=0.2)


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), -0.1, 1.1],
)
def test_invalid_probability_range_and_finiteness_are_rejected(bad_value):
    outcomes = probabilities()
    outcomes[Intent.CONVERSATION] = bad_value
    with pytest.raises(ValidationError):
        decision(probabilities=outcomes)


def test_missing_unknown_and_wrong_total_distributions_are_rejected():
    missing = probabilities()
    missing.pop(Intent.CONVERSATION)
    with pytest.raises(ValidationError):
        decision(probabilities=missing)

    unknown = probabilities()
    unknown["not_an_intent"] = 0.0
    with pytest.raises(ValidationError):
        decision(probabilities=unknown)

    wrong_total = probabilities()
    wrong_total[Intent.CONVERSATION] = 0.94
    with pytest.raises(ValidationError):
        decision(probabilities=wrong_total)


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_vendor_confidence_is_rejected(confidence):
    with pytest.raises(ValidationError):
        decision(confidence=confidence)


@given(st.floats(allow_nan=True, allow_infinity=True))
def test_any_accepted_distribution_is_valid_and_has_a_maximum_selected_intent(value):
    outcomes = probabilities()
    outcomes[Intent.CONVERSATION] = value
    try:
        accepted = decision(probabilities=outcomes)
    except ValidationError:
        return
    assert all(math.isfinite(probability) and 0 <= probability <= 1 for probability in accepted.probabilities.values())
    assert abs(math.fsum(accepted.probabilities.values()) - 1) <= 1e-6
    assert accepted.selected_probability == max(accepted.probabilities.values())


def test_probability_mapping_is_immutable_and_copied_from_the_caller():
    outcomes = probabilities()
    accepted = decision(probabilities=outcomes)
    with pytest.raises(TypeError):
        accepted.probabilities[Intent.CONVERSATION] = 0.1
    outcomes[Intent.CONVERSATION] = 0.0
    assert accepted.probabilities[Intent.CONVERSATION] == pytest.approx(0.95)
    assert json.loads(accepted.model_dump_json())["probabilities"]["conversation"] == pytest.approx(0.95)
    reordered = dict(reversed(list(probabilities().items())))
    assert decision(probabilities=reordered).model_dump_json() == decision().model_dump_json()


def test_nested_tool_parameters_are_immutable_and_json_serializable():
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
        "required": ["city"],
    }
    tool = ToolSpec(name="weather", parameters=parameters)

    with pytest.raises(TypeError):
        tool.parameters["type"] = "array"
    with pytest.raises(TypeError):
        tool.parameters["properties"]["city"]["type"] = "number"
    with pytest.raises((AttributeError, TypeError)):
        tool.parameters["required"].append("days")

    parameters["properties"]["city"]["type"] = "number"
    parameters["required"].append("days")
    assert tool.parameters["properties"]["city"]["type"] == "string"
    assert tool.parameters["required"] == ("city",)
    json.dumps(tool.model_dump(mode="json"))

    reordered = {
        "required": ["city"],
        "properties": {"days": {"type": "integer"}, "city": {"type": "string"}},
        "type": "object",
    }
    assert ToolSpec(name="weather", parameters=reordered).model_dump_json() == tool.model_dump_json()
    assert ToolSpec(name="empty").parameters == {}
    with pytest.raises(TypeError):
        ToolSpec(name="empty").parameters["type"] = "object"


@pytest.mark.parametrize("parameters", [{"x": {1: "not-a-string-key"}}, {"x": float("nan")}])
def test_non_json_tool_parameters_are_rejected(parameters):
    with pytest.raises(ValidationError):
        ToolSpec(name="weather", parameters=parameters)


def test_settings_transforms_do_not_mutate_inputs():
    settings = UserSettings()
    slowed = apply_settings_action(settings, AdjustSpeakingSpeed(delta=-0.1))
    assert settings.speaking_speed == 1.0 and slowed.speaking_speed == 0.9
    assert slowed.revision == 1 and settings.revision == 0
    assert apply_settings_action(settings, SetSpeakingSpeed(speed=1.0)) is settings
    assert apply_settings_action(settings, SetCaptionsVisible(visible=False)).captions_visible is False
    assert settings.captions_visible is True
    assert apply_settings_action(settings, SelectApprovedVoice(voice_id="voice-1")).approved_voice_id == "voice-1"
    assert apply_settings_action(settings, OpenMemoryExplorer()) is settings
    assert apply_settings_action(settings, ShowConversationHistory()) is settings
    assert apply_settings_action(settings, SearchMemory(query="trip")) is settings
    assert apply_settings_action(settings, AdjustSpeakingSpeed(delta=-0.1)).revision == 1
    assert (
        apply_settings_action(UserSettings(speaking_speed=0.8), AdjustSpeakingSpeed(delta=-0.1)).speaking_speed == 0.8
    )


def test_memory_and_request_permissions():
    with pytest.raises(ValidationError):
        memory(embedding=tuple([0.0] * 3))
    assert memory(embedding=tuple([0.0] * 1536)).embedding is not None
    with pytest.raises(ValidationError):
        ContextSelection(
            revision=1,
            included=(ContextEntry(id="memory-1", role="memory", text="x"),),
            excluded_ids=frozenset({"memory-1"}),
        )
    selection = ContextSelection(revision=1, excluded_ids=frozenset({"memory-1"}))
    policy = resolve_response_policy()
    with pytest.raises(ValidationError):
        ModelRequest(
            turn_id="turn-1",
            attempt_id="attempt-1",
            instructions="System rules.",
            selection=selection,
            response_policy=policy,
            settings=UserSettings(),
            memories=(memory(),),
        )
    tool = ToolSpec(name="weather", parameters={"type": "object"})
    request = ModelRequest(
        turn_id="turn-1",
        attempt_id="attempt-1",
        instructions="System rules.",
        selection=selection,
        response_policy=policy,
        settings=UserSettings(),
        tools=(tool,),
    )
    assert request.language == "en" and request.memories == ()
    with pytest.raises(TypeError):
        request.tools[0].parameters["type"] = "array"


@given(
    speed=st.sampled_from([0.8, 0.9, 1.0, 1.1, 1.2]),
    delta=st.sampled_from([-0.2, -0.1, 0.1, 0.2]),
)
def test_speed_transform_is_bounded_deterministic_and_immutable(speed, delta):
    settings = UserSettings(speaking_speed=speed)
    once = apply_settings_action(settings, AdjustSpeakingSpeed(delta=delta))
    twice = apply_settings_action(settings, AdjustSpeakingSpeed(delta=delta))
    assert once == twice
    assert 0.8 <= once.speaking_speed <= 1.2
    assert abs(round(once.speaking_speed * 10) - once.speaking_speed * 10) < 1e-9
    assert settings.speaking_speed == speed


def test_generated_contract_components_match_models_and_checked_in_schema():
    components = contract_components()
    expected = {model.__name__ for model in (Decision, UserSettings, MemoryRecord, ModelRequest, *ACTION_TYPES)} | {
        "Action"
    }
    assert expected <= set(components)
    assert components["Decision"]["properties"]["turn_id"]["maxLength"] == 100
    assert components["UserSettings"]["properties"]["speaking_speed"]["maximum"] == 1.2
    spec = json.loads((ROOT / "packages/contracts/openapi.json").read_text())
    assert expected <= set(spec["components"]["schemas"])
    for name in expected:
        assert spec["components"]["schemas"][name] == components[name]


def test_checked_in_budget_reservation_matches_runtime_schema():
    from app.routes.worker import BudgetReservation

    spec = json.loads((ROOT / "packages/contracts/openapi.json").read_text())
    assert spec["components"]["schemas"]["BudgetReservation"] == BudgetReservation.model_json_schema()
