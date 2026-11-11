import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

def test_protection_fails_closed():
    with pytest.raises(ValueError,match='WEB_SERVICE_SECRET_REQUIRED'):
        create_app(Settings(_env_file=None,development_protected=True))

def test_api_requires_proxy_secret_and_disables_docs(tmp_path):
    settings=Settings(_env_file=None,development_protected=True,web_service_secret='independent-secret',database_url=f'sqlite+aiosqlite:///{tmp_path}/access.db')
    with TestClient(create_app(settings)) as client:
        for headers in ({},{'x-web-service-secret':'wrong'}):
            assert client.post('/v1/demo-session',headers=headers).status_code==401
        assert client.post('/v1/demo-session',headers={'x-web-service-secret':'independent-secret'}).status_code==200
        assert client.get('/docs').status_code==404
        assert client.get('/openapi.json').status_code==404
        assert client.get('/healthz').status_code==200
