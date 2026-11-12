import os

import pytest
from config import provider_credentials, read_worker_environment, require_values


def test_cli_imported_job_gets_file_credentials_without_mutating_environment(tmp_path):
    file = tmp_path / ".env"
    file.write_text(
        "AGENT_SERVICE_SECRET=local-secret\nAPI_INTERNAL_URL=http://localhost:8000\nOPENAI_API_KEY=local-key\n"
    )
    before = dict(os.environ)
    result = read_worker_environment(file, {})
    assert require_values(result, "AGENT_SERVICE_SECRET", "OPENAI_API_KEY") == {
        "AGENT_SERVICE_SECRET": "local-secret",
        "OPENAI_API_KEY": "local-key",
    }
    assert dict(os.environ) == before


def test_injected_environment_takes_precedence_and_missing_values_fail_closed(tmp_path):
    file = tmp_path / ".env"
    file.write_text("AGENT_SERVICE_SECRET=file-secret\n")
    assert (
        read_worker_environment(file, {"AGENT_SERVICE_SECRET": "injected-secret"})["AGENT_SERVICE_SECRET"]
        == "injected-secret"
    )
    with pytest.raises(ValueError, match="AGENT_SERVICE_SECRET"):
        require_values(read_worker_environment(file, {"AGENT_SERVICE_SECRET": ""}), "AGENT_SERVICE_SECRET")


def test_deepseek_primary_keeps_optional_openai_fallback_key():
    values = {
        "DEEPGRAM_API_KEY": "deepgram",
        "ELEVEN_API_KEY": "eleven",
        "DEEPSEEK_API_KEY": "deepseek",
        "OPENAI_API_KEY": "openai",
    }
    credentials = provider_credentials(values, "deepseek_direct")
    assert credentials["DEEPSEEK_API_KEY"] == "deepseek"
    assert credentials["OPENAI_API_KEY"] == "openai"


def test_deepseek_primary_without_openai_key_remains_primary_only():
    values = {
        "DEEPGRAM_API_KEY": "deepgram",
        "ELEVEN_API_KEY": "eleven",
        "DEEPSEEK_API_KEY": "deepseek",
    }
    credentials = provider_credentials(values, "deepseek_direct")
    assert "OPENAI_API_KEY" not in credentials


def test_openai_primary_does_not_require_deepseek_or_build_fallback_credentials():
    values = {
        "DEEPGRAM_API_KEY": "deepgram",
        "ELEVEN_API_KEY": "eleven",
        "OPENAI_API_KEY": "openai",
    }
    credentials = provider_credentials(values, "openai_direct")
    assert credentials["OPENAI_API_KEY"] == "openai"
    assert "DEEPSEEK_API_KEY" not in credentials
