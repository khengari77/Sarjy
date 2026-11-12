from types import SimpleNamespace

import pytest

import provider_factory

CREDENTIALS = {
    "OPENAI_API_KEY": "openai-test",
    "DEEPSEEK_API_KEY": "deepseek-test",
    "DEEPGRAM_API_KEY": "deepgram-test",
    "ELEVEN_API_KEY": "eleven-test",
}


class Captured:
    """Fake provider that records the exact construction payload."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(provider_factory.openai, "responses", SimpleNamespace(LLM=Captured))
    monkeypatch.setattr(provider_factory.openai, "LLM", Captured)
    monkeypatch.setattr(provider_factory, "OwnedDeepSeekLLM", Captured)
    monkeypatch.setattr(provider_factory, "OwnedOpenAIResponsesLLM", Captured)
    monkeypatch.setattr(provider_factory.openai_sdk, "AsyncOpenAI", lambda **kwargs: ("client", kwargs))
    monkeypatch.setattr(provider_factory.deepgram, "STT", lambda **kwargs: ("stt", kwargs))
    monkeypatch.setattr(provider_factory.elevenlabs, "TTS", lambda **kwargs: ("tts", kwargs))
    return provider_factory.build_providers


def pipeline(provider, model, max_output_tokens, **extra):
    return {
        "stt": {"model": "nova-3", "language": "en"},
        "llm": {"provider": provider, "model": model, "language": "en"},
        "tts": {"model": "eleven_flash_v2_5", "voice_id": "voice", "language": "en"},
        "max_output_tokens": max_output_tokens,
        **extra,
    }


@pytest.mark.parametrize("tier_cap", [64, 1024])
def test_openai_adapter_sends_the_exact_tier_cap_and_disables_reasoning(providers, tier_cap):
    built = providers(pipeline("openai_direct", "gpt-6-luna", tier_cap), CREDENTIALS)
    assert built["llm"].kwargs["reasoning"] == {"effort": "none"}
    assert built["llm"].kwargs["store"] is False
    assert built["llm"].kwargs["client"][1]["max_retries"] == 0


@pytest.mark.parametrize("tier_cap", [64, 1024])
def test_deepseek_adapter_sends_the_exact_tier_cap(providers, tier_cap):
    built = providers(pipeline("deepseek_direct", "deepseek-flash", tier_cap), CREDENTIALS)
    assert built["llm"].kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert built["llm"].kwargs["max_retries"] == 0


def test_deepseek_builds_an_optional_openai_fallback(providers):
    built = providers(pipeline("deepseek_direct", "deepseek-flash", 128), CREDENTIALS)
    assert built["fallback_llm"] is not None
    assert built["fallback_llm"].kwargs["reasoning"] == {"effort": "none"}
    assert built["fallback_llm"].kwargs["store"] is False

    without_openai = dict(CREDENTIALS)
    without_openai.pop("OPENAI_API_KEY")
    assert providers(pipeline("deepseek_direct", "deepseek-flash", 128), without_openai)["fallback_llm"] is None


def test_openai_primary_has_no_fallback(providers):
    built = providers(pipeline("openai_direct", "gpt-6-luna", 128), CREDENTIALS)
    assert built["fallback_llm"] is None


def test_complexity_is_not_forwarded_to_either_adapter(providers):
    # PipelineConfig rejects complexity upstream; even if a stale payload carried
    # it, the adapter payload must be byte-for-byte the same.
    simple = providers(pipeline("openai_direct", "gpt-6-luna", 128), CREDENTIALS)["llm"].kwargs
    complex_ = providers(pipeline("openai_direct", "gpt-6-luna", 128, complexity="complex"), CREDENTIALS)["llm"].kwargs
    assert simple == complex_
    assert simple["reasoning"] == {"effort": "none"}
