from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from test_memory import FakeEmbedder


def test_memory_routes_authenticate_persist_and_reject_forged_ownership(tmp_path):
    embedder=FakeEmbedder()
    settings=Settings(database_url=f'sqlite+aiosqlite:///{tmp_path}/api.db',_env_file=None)
    with TestClient(create_app(settings,embedding_adapter=embedder)) as client:
        assert client.post('/v1/memories/search',json={'conversation_id':'x','query':'city'}).status_code==401
        client.post('/v1/demo-session')
        chat=client.post('/v1/conversations').json()['id']
        payload={'content':'Tripoli','idempotency_key':'once'}
        result=client.post(f'/v1/conversations/{chat}/memories',json=payload)
        assert result.status_code==201,result.text
        memory=result.json()
        assert client.post(f'/v1/conversations/{chat}/memories',json=payload).json()==memory
        second=client.post('/v1/conversations').json()['id']
        found=client.post('/v1/memories/search',json={'conversation_id':second,'query':'city'})
        assert found.status_code==200 and found.json()[0]['id']==memory['id']
        corrected=client.post(f"/v1/memories/{memory['id']}/correct",json={'content':'London','expected_version':1,'idempotency_key':'correct'})
        assert corrected.status_code==200 and corrected.json()['version']==2
        assert client.post(f'/v1/conversations/{chat}/memories',json=payload|{'owner_id':'forged'}).status_code==422
        other=TestClient(client.app)
        other.post('/v1/demo-session')
        before=len(embedder.calls)
        assert other.post(f'/v1/conversations/{chat}/memories',json=payload).status_code==404
        assert other.post('/v1/memories/search',json={'conversation_id':chat,'query':'city'}).status_code==404
        assert other.post(f"/v1/memories/{memory['id']}/correct",json={'content':'Paris','expected_version':1,'idempotency_key':'other'}).status_code==404
        assert len(embedder.calls)==before
