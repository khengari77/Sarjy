import json
from pathlib import Path

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError

from app.domain.contracts import (
    ACTION_TYPES,
    Action,
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
    TIER_CAPS,
    UserSettings,
    apply_settings_action,
    contract_components,
    parse_action,
    resolve_response_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def probabilities(chosen=Intent.CONVERSATION, value=0.95):
    rest = (1 - value) / (len(Intent) - 1)
    return {intent: (value if intent is chosen else rest) for intent in Intent}


def decision(**overrides):
    values = {
        'turn_id': 'turn-1',
        'intent': Intent.CONVERSATION,
        'probabilities': probabilities(),
        'confidence': 0.95,
        'tier': 2,
        'complexity': Complexity.SIMPLE,
        'source': DecisionSource.JEV,
        'model': 'jev-1.14.0',
        'policy_version': 'v1',
    }
    values.update(overrides)
    return Decision(**values)


def memory(identifier='memory-1', **overrides):
    values = {
        'id': identifier,
        'owner_id': 'owner-1',
        'content': 'The user lives in Tripoli.',
        'source': MemorySource(turn_id='turn-1', conversation_id='conversation-1', created_at='2026-11-11T00:00:00+00:00'),
        'version': 1,
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
    # A saved preference limits the tier; an explicit request still wins within bounds.
    assert resolve_response_policy(ResponsePreference.BRIEF, explicit_tier=5).tier == 2
    assert resolve_response_policy(ResponsePreference.BALANCED, explicit_tier=3).tier == 3


def test_unknown_actions_and_invalid_arguments_are_rejected():
    with pytest.raises(ValidationError):
        parse_action({'kind': 'DeleteEverything'})
    with pytest.raises(ValidationError):
        parse_action({'kind': 'SetCaptionsVisible', 'visible': True, 'extra': 1})
    with pytest.raises(ValidationError):
        AdjustSpeakingSpeed(delta=0.05)
    with pytest.raises(ValidationError):
        AdjustSpeakingSpeed(delta=0)
    with pytest.raises(ValidationError):
        SetSpeakingSpeed(speed=1.25)
    with pytest.raises(ValidationError):
        UserSettings(speaking_speed=1.05)
    with pytest.raises(ValidationError):
        SelectApprovedVoice(voice_id='bad voice!')


def test_every_shared_action_round_trips_through_the_discriminator():
    payloads = [
        {'kind': 'AdjustSpeakingSpeed', 'delta': -0.1},
        {'kind': 'SetSpeakingSpeed', 'speed': 1.2},
        {'kind': 'SelectApprovedVoice', 'voice_id': 'voice-1'},
        {'kind': 'ShowConversationHistory'},
        {'kind': 'OpenMemoryExplorer'},
        {'kind': 'SetCaptionsVisible', 'visible': False},
        {'kind': 'SearchMemory', 'query': 'trip', 'limit': 5},
        {'kind': 'ProposeContextExclusion', 'query': 'trip', 'keep': ['budget']},
        {'kind': 'RestoreContextOperation', 'operation_id': 'operation-1'},
    ]
    assert {type(parse_action(payload)) for payload in payloads} == set(ACTION_TYPES)


def test_decision_rejects_inconsistent_or_unknown_classifications():
    with pytest.raises(ValidationError):
        decision(probabilities={Intent.CONVERSATION: 0.4, Intent.AMBIGUOUS: 0.4})
    with pytest.raises(ValidationError):
        decision(confidence=0.5)
    with pytest.raises(ValidationError):
        decision(intent='delete_everything')
    with pytest.raises(ValidationError):
        decision(tier=9)
    assert decision().permits_automatic_action()
    assert not decision(confidence=0.6, probabilities=probabilities(value=0.6)).permits_automatic_action()
    assert not decision(intent=Intent.AMBIGUOUS, probabilities=probabilities(Intent.AMBIGUOUS)).permits_automatic_action()
    close = {intent: 0.125 for intent in Intent}
    assert not decision(probabilities=close, confidence=0.125).permits_automatic_action()


def test_settings_transforms_do_not_mutate_inputs():
    settings = UserSettings()
    slowed = apply_settings_action(settings, AdjustSpeakingSpeed(delta=-0.1))
    assert settings.speaking_speed == 1.0 and slowed.speaking_speed == 0.9
    assert slowed.revision == 1 and settings.revision == 0
    assert apply_settings_action(settings, SetSpeakingSpeed(speed=1.0)) is settings
    assert apply_settings_action(settings, SetCaptionsVisible(visible=False)).captions_visible is False
    assert settings.captions_visible is True
    assert apply_settings_action(settings, SelectApprovedVoice(voice_id='voice-1')).approved_voice_id == 'voice-1'
    assert apply_settings_action(settings, OpenMemoryExplorer()) is settings
    assert apply_settings_action(settings, ShowConversationHistory()) is settings
    assert apply_settings_action(settings, SearchMemory(query='trip')) is settings
    assert apply_settings_action(settings, AdjustSpeakingSpeed(delta=-0.1)).revision == 1
    assert apply_settings_action(UserSettings(speaking_speed=0.8), AdjustSpeakingSpeed(delta=-0.1)).speaking_speed == 0.8


def test_memory_and_request_permissions():
    with pytest.raises(ValidationError):
        memory(embedding=tuple([0.0] * 3))
    assert memory(embedding=tuple([0.0] * 1536)).embedding is not None
    with pytest.raises(ValidationError):
        ContextSelection(revision=1, included=(ContextEntry(id='memory-1', role='memory', text='x'),), excluded_ids=frozenset({'memory-1'}))
    selection = ContextSelection(revision=1, excluded_ids=frozenset({'memory-1'}))
    policy = resolve_response_policy()
    with pytest.raises(ValidationError):
        ModelRequest(
            turn_id='turn-1', attempt_id='attempt-1', instructions='System rules.',
            selection=selection, response_policy=policy, settings=UserSettings(),
            memories=(memory(),),
        )
    request = ModelRequest(
        turn_id='turn-1', attempt_id='attempt-1', instructions='System rules.',
        selection=selection, response_policy=policy, settings=UserSettings(),
    )
    assert request.language == 'en' and request.memories == ()


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
    expected = {model.__name__ for model in (Decision, UserSettings, MemoryRecord, ModelRequest, *ACTION_TYPES)} | {'Action'}
    assert expected <= set(components)
    assert components['Decision']['properties']['turn_id']['maxLength'] == 100
    assert components['UserSettings']['properties']['speaking_speed']['maximum'] == 1.2
    spec = json.loads((ROOT / 'packages/contracts/openapi.json').read_text())
    assert expected <= set(spec['components']['schemas'])
    for name in expected:
        assert spec['components']['schemas'][name] == components[name]
