from types import MappingProxyType

import pytest
from app.domain.contracts import (
    AdjustSpeakingSpeed,
    Complexity,
    Decision,
    DecisionSource,
    Intent,
    ResponsePreference,
    SelectApprovedVoice,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    UserSettings,
    apply_settings_action,
)
from hypothesis import given
from hypothesis import strategies as st


def settings(**overrides) -> UserSettings:
    values = {
        "speaking_speed": 1.0,
        "captions_visible": True,
        "approved_voice_id": "voice_a",
        "response_preference": ResponsePreference.AUTOMATIC,
        "revision": 3,
    }
    values.update(overrides)
    return UserSettings(**values)


@pytest.mark.parametrize(
    "action",
    [SetSpeakingSpeed(speed=1.0), SetCaptionsVisible(visible=True), SelectApprovedVoice(voice_id="voice_a")],
)
def test_noop_settings_action_returns_the_same_revision(action):
    original = settings()
    assert apply_settings_action(original, action) is original


def test_setting_change_increments_revision_once_and_preserves_other_fields():
    original = settings()
    updated = apply_settings_action(original, AdjustSpeakingSpeed(delta=0.1))
    assert updated.speaking_speed == 1.1
    assert updated.revision == original.revision + 1
    assert updated.captions_visible == original.captions_visible
    assert updated.approved_voice_id == original.approved_voice_id
    assert original.speaking_speed == 1.0


@given(
    st.integers(min_value=8, max_value=12).map(lambda step: step / 10),
    st.sampled_from([-0.1, 0.1]),
)
def test_speed_adjustment_never_leaves_bounds(speed, delta):
    updated = apply_settings_action(settings(speaking_speed=speed), AdjustSpeakingSpeed(delta=delta))
    assert 0.8 <= updated.speaking_speed <= 1.2
    assert updated.revision in {3, 4}


def test_decision_probabilities_are_an_immutable_snapshot():
    probabilities = {intent: 1 / len(Intent) for intent in Intent}
    decision = Decision(
        turn_id="turn",
        intent=Intent.CONVERSATION,
        probabilities=probabilities,
        confidence=1.0,
        tier=2,
        complexity=Complexity.SIMPLE,
        source=DecisionSource.LOCAL_EXACT,
        model="test-model",
        policy_version="v1",
    )
    assert isinstance(decision.probabilities, MappingProxyType)
    with pytest.raises(TypeError):
        decision.probabilities[Intent.CONVERSATION] = 1.0
    assert probabilities[Intent.CONVERSATION] == 1 / len(Intent)
