"""Optional DeepSeek spending guard with provider-specific accounting."""
import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


HEADERS = {'x-agent-secret': 'test-service-secret'}


def configured(tmp_path, **overrides):
    file = tmp_path / 'deepseek.db'
    settings = Settings(database_url=f'sqlite+aiosqlite:///{file}',
        agent_service_secret='test-service-secret', _env_file=None, **overrides)
    return file, create_app(settings)


def reserve(client, provider='deepseek'):
    return client.post('/internal/development/reserve', headers=HEADERS, json={'provider': provider})


def test_deepseek_ceiling_is_provider_specific_and_persistent(tmp_path):
    file, app = configured(tmp_path, deepseek_daily_micro_usd=4000, deepseek_request_micro_usd=2000)
    with TestClient(app) as client:
        assert reserve(client).status_code == 200
        assert reserve(client).status_code == 200
        denied = reserve(client)
        assert denied.status_code == 429
        assert denied.json()['detail']['code'] == 'DAILY_BUDGET_EXHAUSTED'
        assert reserve(client, 'openai').status_code == 200
    with sqlite3.connect(file) as db:
        rows = dict(db.execute('SELECT provider, reserved_units FROM development_budgets').fetchall())
        assert rows == {'deepseek': 4000, 'openai': 20_000}
    # A restarted app must see the committed ceiling.
    with TestClient(create_app(Settings(database_url=f'sqlite+aiosqlite:///{file}',
            agent_service_secret='test-service-secret', deepseek_daily_micro_usd=4000,
            deepseek_request_micro_usd=2000, _env_file=None))) as restarted:
        assert reserve(restarted).status_code == 429


def test_disabled_guard_sends_without_a_ledger(tmp_path):
    file, app = configured(tmp_path, deepseek_budget_enabled=False, deepseek_daily_micro_usd=0)
    with TestClient(app) as client:
        for _ in range(5):
            response = reserve(client)
            assert response.status_code == 200
            assert response.json()['enabled'] is False
    with sqlite3.connect(file) as db:
        assert db.execute('SELECT COUNT(*) FROM development_budgets').fetchone()[0] == 0
