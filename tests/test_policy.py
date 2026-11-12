import ast
from pathlib import Path

import pytest
from app.domain.contracts import (
    PREFERENCE_MAX_TIER,
    TIER_CAPS,
    ResponsePolicy,
    ResponsePreference,
    resolve_response_policy,
)
from app.domain.pipeline import PipelineConfig
from app.domain.policy import (
    BASE_INSTRUCTIONS,
    LEGACY_OUTPUT_TOKENS,
    VOICE_RULES,
    compose_system_message,
    length_guidance,
    normalize_language,
    pipeline_instructions,
    pipeline_policy,
)
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

WORD_TARGETS = {1: 15, 2: 40, 3: 90, 4: 180, 5: 350}
ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DOMAIN_IMPORTS = {
    "aiohttp",
    "asyncpg",
    "fastapi",
    "httpx",
    "openai",
    "requests",
    "sqlalchemy",
}


def test_policy_module_stays_a_pure_functional_core():
    """The domain layer must not reach into I/O, providers or the database."""
    tree = ast.parse((ROOT / "apps/api/app/domain/policy.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & FORBIDDEN_DOMAIN_IMPORTS


def config(**overrides):
    values = {
        "stt": {"provider": "deepgram_direct", "model": "nova-3", "language": "en"},
        "llm": {"provider": "openai_direct", "model": "gpt-6-luna", "language": "en"},
        "tts": {"provider": "elevenlabs_direct", "model": "eleven_flash_v2_5", "voice_id": "voice", "language": "en"},
    }
    values.update(overrides)
    return PipelineConfig(**values)


def test_every_tier_derives_its_exact_cap_and_cannot_be_overridden():
    assert TIER_CAPS == (64, 128, 256, 512, 1024)
    for tier, cap in enumerate(TIER_CAPS, start=1):
        built = config(response_tier=tier)
        assert built.max_output_tokens == cap
        assert built.model_dump()["max_output_tokens"] == cap
    # The cap is derived output, never client input.
    with pytest.raises(ValidationError):
        config(max_output_tokens=64)
    with pytest.raises(ValidationError):
        config(max_output_tokens=96)
    with pytest.raises(ValidationError):
        config(response_tier=0)
    with pytest.raises(ValidationError):
        config(response_tier=6)


def test_frozen_config_cannot_drift_after_construction():
    built = config()
    with pytest.raises(ValidationError):
        built.response_tier = 5
    with pytest.raises(ValidationError):
        built.llm.provider = "deepseek_direct"
    assert built.response_tier == 2 and built.max_output_tokens == 128


def test_classified_tiers_are_bounded_by_saved_preference():
    for preference, limit in PREFERENCE_MAX_TIER.items():
        assert resolve_response_policy(preference, classified_tier=5).tier == limit


def test_resolved_configuration_preserves_explicit_preference_overrides():
    assert config(response_tier=5, response_preference="brief").max_output_tokens == 1024
    assert config(response_tier=3, response_preference="balanced").response_tier == 3
    assert config(response_tier=5, response_preference="detailed").max_output_tokens == 1024


def test_complexity_cannot_change_policy_or_reach_the_provider_config():
    # Complexity is deliberately absent from the session config, so it cannot
    # alter reasoning or instructions even by accident.
    with pytest.raises(ValidationError):
        config(complexity="complex")
    for tier in range(1, 6):
        policy = resolve_response_policy(ResponsePreference.AUTOMATIC, tier)
        assert compose_system_message("en", policy) == compose_system_message("en", policy)


def test_guidance_targets_words_and_never_exposes_caps_or_tokens():
    for tier, words in WORD_TARGETS.items():
        guidance = length_guidance(tier, "en")
        assert f"{words} words" in guidance
        assert not any(str(cap) in guidance for cap in TIER_CAPS)
        assert "token" not in guidance.lower()


def test_arabic_guidance_is_authored_separately_and_avoids_encouraging_token_counts():
    for tier in range(1, 6):
        arabic = length_guidance(tier, "ar")
        assert arabic != length_guidance(tier, "en")
        assert arabic.strip() == arabic and arabic
        assert not any(str(cap) in arabic for cap in TIER_CAPS)
    assert normalize_language("ar") == "ar"
    assert normalize_language("fr") == "en"
    assert normalize_language(None) == "en"
    with pytest.raises(ValueError):
        length_guidance(0, "en")
    with pytest.raises(ValueError):
        length_guidance(6, "en")


def test_composed_message_is_complete_and_deterministic():
    policy = resolve_response_policy(ResponsePreference.BALANCED, explicit_tier=3)
    message = compose_system_message("en", policy)
    assert message.startswith(BASE_INSTRUCTIONS["en"])
    assert length_guidance(3, "en") in message
    assert message.endswith(VOICE_RULES["en"])
    assert message == compose_system_message("en", policy)
    arabic = compose_system_message("ar", policy)
    assert arabic.startswith(BASE_INSTRUCTIONS["ar"]) and arabic != message


def test_stored_pipeline_recovers_policy_including_legacy_sessions():
    assert pipeline_policy({}).tier == 2
    assert pipeline_policy({"max_output_tokens": LEGACY_OUTPUT_TOKENS}).max_output_tokens == 128
    assert pipeline_policy({"max_output_tokens": 512}).tier == 4
    assert pipeline_policy({"response_tier": 5}).max_output_tokens == 1024
    # A contradictory or malformed stored value is repaired instead of trusted.
    assert pipeline_policy({"response_tier": 5, "response_preference": "brief"}).tier == 5
    assert pipeline_policy({"response_preference": "nonsense"}).tier == 2
    assert pipeline_policy({"response_tier": "not-a-tier"}).tier == 2
    assert pipeline_policy({"response_tier": 99}).tier == 5
    assert pipeline_policy({"max_output_tokens": 999}).tier == 2
    assert pipeline_instructions({"llm": {"language": "ar"}}) == compose_system_message(
        "ar", ResponsePolicy.for_tier(2)
    )


@given(
    tier=st.integers(min_value=1, max_value=5),
    language=st.sampled_from(["en", "ar"]),
    preference=st.sampled_from(list(ResponsePreference)),
)
def test_policy_properties_hold_for_every_tier_language_and_preference(tier, language, preference):
    policy = resolve_response_policy(preference, tier)
    message = compose_system_message(language, policy)
    assert policy.tier == tier
    assert policy.max_output_tokens == TIER_CAPS[policy.tier - 1]
    assert message == compose_system_message(language, policy)
    assert length_guidance(policy.tier, language) in message
    assert not any(str(cap) in message for cap in TIER_CAPS)


@given(tier=st.integers(1, 5), preference=st.sampled_from(list(ResponsePreference)))
def test_normalized_configuration_is_idempotent_and_does_not_mutate(tier, preference):
    from app.domain.policy import normalize_pipeline

    source = {"response_tier": tier, "response_preference": preference.value}
    before = dict(source)
    resolved = normalize_pipeline(source)
    assert source == before
    assert normalize_pipeline(resolved) == resolved
    assert resolved["max_output_tokens"] == TIER_CAPS[tier - 1]
