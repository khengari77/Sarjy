import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_worker_turn_retry_snapshot_and_finalization(tmp_path):
    file = tmp_path/'worker.db'
    settings = Settings(database_url=f'sqlite+aiosqlite:///{file}', agent_service_secret='test-service-secret', _env_file=None)
    with TestClient(create_app(settings)) as client:
        owner = client.post('/v1/demo-session').json()['id']
        chat = client.post('/v1/conversations').json()['id']
        with sqlite3.connect(file) as db:
            db.execute('INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)',('session',chat,owner,'room','active',(datetime.now(UTC)+timedelta(minutes=10)).isoformat(),json.dumps({})))
        headers = {'x-agent-secret':'test-service-secret','x-session-id':'session','x-room-name':'room'}
        first = client.post('/internal/agent/turn/start',headers=headers,json={'request_id':'once','text':'Hello'})
        assert first.status_code == 200
        assert client.post('/internal/agent/turn/start',headers=headers,json={'request_id':'once','text':'Hello'}).json() == first.json()
        result = first.json()
        assert result['context']['included_record_ids'] == ['system',result['turn_id']]
        body = {'turn_id':result['turn_id'],'text':'Welcome','status':'completed'}
        final = client.post('/internal/agent/turn/finalize',headers=headers,json=body)
        assert client.post('/internal/agent/turn/finalize',headers=headers,json=body).json() == final.json()
        history = client.get(f'/v1/conversations/{chat}/turns').json()
        assert [t['text'] for t in history] == ['Hello','Welcome']
        invalid = headers | {'x-room-name':'another-room'}
        assert client.post('/internal/agent/bootstrap',headers=invalid).status_code == 403
