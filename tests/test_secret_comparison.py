import pytest
from app.config import Settings
from app.domain.context_operations import sign_proposal, verify_proposal
from app.domain.secrets import secret_matches
from app.main import create_app
from fastapi.testclient import TestClient


def settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "development_protected": True,
        "web_service_secret": "independent-secret",
        "agent_service_secret": "agent-secret",
        "deepseek_budget_enabled": False,
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/secrets.db",
    }
    values.update(overrides)
    return Settings(**values)


def test_secret_matches_is_closed_for_malformed_values():
    assert secret_matches("agent-secret", "agent-secret") is True
    assert secret_matches("sécret", "sécret") is True
    assert secret_matches("agent-secret", "another-secret") is False
    assert secret_matches("", "agent-secret") is False
    assert secret_matches("sécret", "secret") is False
    assert secret_matches(None, "agent-secret") is False
    assert secret_matches("agent-secret", "") is False


def test_non_ascii_web_secret_is_denied_without_server_error(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post("/v1/demo-session", headers={b"x-web-service-secret": b"\xe9"})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "WEB_AUTH_REQUIRED"


def test_non_ascii_worker_secret_is_denied_without_server_error(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/internal/development/reserve",
            headers={b"x-agent-secret": b"\xe9"},
            json={"provider": "deepseek", "characters": 0},
        )
        assert response.status_code == 401


def test_configured_secrets_still_authenticate(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert (
            client.post("/v1/demo-session", headers={"x-web-service-secret": "independent-secret"}).status_code == 200
        )
        reserve = client.post(
            "/internal/development/reserve",
            headers={"x-agent-secret": "agent-secret"},
            json={"provider": "deepseek", "characters": 0},
        )
        assert reserve.status_code == 200
        assert reserve.json()["enabled"] is False


def test_non_ascii_proposal_signature_is_invalid_not_a_type_error():
    token = sign_proposal({"kind": "keep"}, "proposal-secret")
    body, _ = token.rsplit(".", 1)
    with pytest.raises(ValueError, match="PROPOSAL_INVALID"):
        verify_proposal(f"{body}.é", "proposal-secret")
