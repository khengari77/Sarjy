from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_identity_isolation_and_durable_history(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        openai_api_key="",
        deepseek_api_key="",
        deepgram_api_key="",
        eleven_api_key="",
        _env_file=None,
    )
    application = create_app(settings)
    with TestClient(application) as alice, TestClient(application) as bob:
        assert alice.get("/v1/conversations").status_code == 401
        aid = alice.post("/v1/demo-session").json()["id"]
        assert alice.post("/v1/demo-session").json()["id"] == aid
        conversation = alice.post("/v1/conversations").json()["id"]
        assert alice.get(f"/v1/conversations/{conversation}/turns").json() == []
        assert bob.post("/v1/demo-session").json()["id"] != aid
        assert bob.get("/v1/conversations").json() == []
        assert bob.get(f"/v1/conversations/{conversation}/turns").status_code == 404
        assert alice.get("/v1/conversations").json()[0]["id"] == conversation


def test_unconfigured_voice_and_worker_spoofing(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        openai_api_key="",
        deepseek_api_key="",
        deepgram_api_key="",
        eleven_api_key="",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        result = client.post("/v1/voice/sessions", json={"conversation_id": chat})
        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "PIPELINE_NOT_CONFIGURED"
        assert client.post("/internal/agent/bootstrap").status_code == 401
        assert client.get("/v1/pipelines/providers").json()["llm"]["configured"] is False
