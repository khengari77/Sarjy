"""Deepgram TTS provider (#175). Provider boundaries are mocked; no network call."""

import asyncio

import provider_factory
import pytest
from app.config import Settings
from app.domain.deepgram_voices import DEEPGRAM_VOICES, DEFAULT_DEEPGRAM_VOICE_ID
from app.domain.pipeline import ModelChoice, PipelineConfig, validate_pipeline
from app.main import create_app
from app.services.voices import DeepgramVoices, VoiceCatalogError, approved_voice_ids
from config import provider_credentials
from fastapi.testclient import TestClient

CREDENTIALS = {"DEEPGRAM_API_KEY": "dg-test", "ELEVEN_API_KEY": "el-test", "DEEPSEEK_API_KEY": "ds-test"}


def worker_pipeline(tts):
    return {
        "stt": {"model": "nova-3", "language": "en"},
        "llm": {"provider": "deepseek_direct", "model": "deepseek-flash", "language": "en"},
        "tts": tts,
    }


@pytest.fixture
def captured(monkeypatch):
    calls = {}
    monkeypatch.setattr(provider_factory.deepgram, "TTS", lambda **kw: calls.setdefault("deepgram", kw) or kw)
    monkeypatch.setattr(provider_factory.elevenlabs, "TTS", lambda **kw: calls.setdefault("elevenlabs", kw) or kw)
    return calls


def test_factory_builds_deepgram_with_voice_as_model(captured):
    provider_factory.build_tts(
        worker_pipeline(
            {"provider": "deepgram_direct", "model": "aura-2", "voice_id": "aura-2-thalia-en", "language": "en"}
        ),
        CREDENTIALS,
    )
    assert captured["deepgram"] == {
        "model": "aura-2-thalia-en",
        "encoding": "linear16",
        "sample_rate": 24000,
        "api_key": "dg-test",
    }
    assert "elevenlabs" not in captured


def test_factory_keeps_elevenlabs_path(captured):
    provider_factory.build_tts(
        worker_pipeline(
            {"provider": "elevenlabs_direct", "model": "eleven_flash_v2_5", "voice_id": "v", "language": "ar"}
        ),
        CREDENTIALS,
    )
    assert captured["elevenlabs"]["voice_id"] == "v"
    assert captured["elevenlabs"]["api_key"] == "el-test"
    assert "deepgram" not in captured


def test_factory_refuses_arabic_under_deepgram(captured):
    with pytest.raises(ValueError, match="TTS_LANGUAGE_UNSUPPORTED"):
        provider_factory.build_tts(
            worker_pipeline(
                {"provider": "deepgram_direct", "model": "aura-2", "voice_id": "aura-2-thalia-en", "language": "ar"}
            ),
            CREDENTIALS,
        )
    assert captured == {}


def test_budget_provider_follows_active_tts_provider():
    assert provider_factory.tts_budget_provider({"tts": {"provider": "deepgram_direct"}}) == "deepgram_tts"
    assert provider_factory.tts_budget_provider({"tts": {"provider": "elevenlabs_direct"}}) == "elevenlabs"


def test_deepgram_needs_no_elevenlabs_credential():
    values = {"DEEPGRAM_API_KEY": "d", "DEEPSEEK_API_KEY": "s"}
    assert "ELEVEN_API_KEY" not in provider_credentials(values, "deepseek_direct", "deepgram_direct")
    with pytest.raises(ValueError, match="ELEVEN_API_KEY"):
        provider_credentials(values, "deepseek_direct", "elevenlabs_direct")


class RecordingTTS:
    def __init__(self):
        self.updates = []

    def update_options(self, **kwargs):
        self.updates.append(kwargs)


def test_speed_controller_uses_update_options_and_clamps():
    control = provider_factory.DeepgramSpeedController("aura-2-thalia-en")
    tts = RecordingTTS()
    assert control.apply(tts, 1.0) == 1.0
    assert tts.updates == []
    assert control.apply(tts, 5) == provider_factory.SPEAKING_SPEED_MAX
    assert control.apply(tts, 5) == provider_factory.SPEAKING_SPEED_MAX
    assert control.apply(tts, 0.1) == provider_factory.SPEAKING_SPEED_MIN
    assert tts.updates == [{"speed": 1.2}, {"speed": 0.8}]


def test_speed_refused_for_unsupported_voice():
    control = provider_factory.DeepgramSpeedController("aura-2-something-de")
    tts = RecordingTTS()
    assert control.apply(tts, 1.1) is None
    assert tts.updates == []


def test_speed_controller_selection():
    assert isinstance(
        provider_factory.speed_controller_for({"tts": {"provider": "deepgram_direct", "voice_id": "x-en"}}),
        provider_factory.DeepgramSpeedController,
    )
    assert isinstance(provider_factory.speed_controller_for({}), provider_factory.SpeakingSpeedController)


def test_static_catalogue_is_well_formed():
    ids = [voice.id for voice in DEEPGRAM_VOICES]
    assert len(ids) == len(set(ids)) == 41
    assert DEFAULT_DEEPGRAM_VOICE_ID in ids
    assert all(v.id.startswith("aura-2-") and v.id.endswith("-en") for v in DEEPGRAM_VOICES)
    assert {v.gender for v in DEEPGRAM_VOICES} == {"male", "female"}
    thalia = next(v for v in DEEPGRAM_VOICES if v.id == DEFAULT_DEEPGRAM_VOICE_ID)
    assert (thalia.gender, thalia.accent, thalia.age) == ("female", "american", "adult")


def test_catalogue_service_requires_known_ids_and_finds_gender():
    catalog = DeepgramVoices()
    assert asyncio.run(catalog.require("aura-2-zeus-en")).gender == "male"
    with pytest.raises(VoiceCatalogError) as error:
        asyncio.run(catalog.require("aura-2-nobody-en"))
    assert error.value.code == "VOICE_UNAVAILABLE"
    approved = approved_voice_ids("aura-2-zeus-en", DEFAULT_DEEPGRAM_VOICE_ID)
    assert asyncio.run(catalog.find_by_trait("gender", "male", approved)).id == "aura-2-zeus-en"
    assert asyncio.run(catalog.find_by_trait("gender", "female", approved)).id == DEFAULT_DEEPGRAM_VOICE_ID
    assert asyncio.run(catalog.find_by_trait("gender", "male", frozenset({DEFAULT_DEEPGRAM_VOICE_ID}))) is None
    assert asyncio.run(catalog.find_by_name("ZEUS")).id == "aura-2-zeus-en"
    assert len(asyncio.run(catalog.list()).voices) == 41


def test_config_validation_and_defaults():
    settings = Settings(_env_file=None)
    assert settings.tts_provider == "deepgram"
    assert settings.default_voice_id == DEFAULT_DEEPGRAM_VOICE_ID
    assert Settings(tts_provider="elevenlabs", _env_file=None).default_voice_id == ""
    with pytest.raises(ValueError):
        Settings(tts_provider="nope", _env_file=None)
    with pytest.raises(ValueError):
        Settings(deepgram_daily_characters=0, _env_file=None)


def test_deepgram_budget_is_a_separate_configurable_ledger(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/dg.db",
        agent_service_secret="t",
        deepgram_daily_characters=700,
        _env_file=None,
    )
    headers = {"x-agent-secret": "t"}
    url = "/internal/development/reserve"
    with TestClient(create_app(settings)) as client:
        first = client.post(url, headers=headers, json={"provider": "deepgram_tts", "characters": 500})
        assert first.json()["unit"] == "characters"
        assert (
            client.post(url, headers=headers, json={"provider": "deepgram_tts", "characters": 201}).status_code == 429
        )
        assert client.post(url, headers=headers, json={"provider": "elevenlabs", "characters": 500}).status_code == 200


def test_pipeline_validation_for_deepgram():
    def pipeline(language, voice="aura-2-thalia-en"):
        return PipelineConfig(
            stt={"provider": "deepgram_direct", "model": "nova-3", "language": language},
            llm={"provider": "deepseek_direct", "model": "deepseek-flash", "language": language},
            tts=ModelChoice(provider="deepgram_direct", model="aura-2", voice_id=voice, language=language),
        )

    keys = {"DEEPGRAM_API_KEY", "DEEPSEEK_API_KEY"}
    assert validate_pipeline(pipeline("en"), keys) == []
    assert {"role": "tts", "code": "TTS_LANGUAGE_UNSUPPORTED"} in validate_pipeline(pipeline("ar"), keys)
    assert {"role": "tts", "code": "MISSING_CREDENTIAL"} in validate_pipeline(pipeline("en"), {"DEEPSEEK_API_KEY"})


def test_arabic_voice_session_fails_with_named_error(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/ar.db", _env_file=None)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        result = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "ar"})
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "TTS_LANGUAGE_UNSUPPORTED"
