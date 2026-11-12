import copy
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from app.config import Settings
from app.main import create_app
from app.services.voices import ElevenLabsVoices, public_catalog


@given(st.lists(st.tuples(st.text(min_size=1), st.text()), max_size=20))
def test_catalog_projection_is_pure_and_exposes_only_voice_choices(entries):
    payload = {'voices': [{'voice_id':id, 'name':name, 'samples':[{'secret':'private recording'}], 'settings':{'private':'configuration'}} for id,name in entries], 'has_more':False}
    original = copy.deepcopy(payload)
    result = public_catalog(payload)
    assert payload == original
    assert result == public_catalog(payload)
    assert [(voice.id,voice.name) for voice in result.voices] == entries
    for voice in result.model_dump()['voices']:
        assert set(voice) == {'id','name','languages'}


def configured(tmp_path):
    return Settings(default_llm_provider='openai_direct', database_url=f'sqlite+aiosqlite:///{tmp_path}/voice.db', livekit_url='wss://test.invalid', livekit_api_key='test-key', livekit_api_secret='test-secret-with-enough-characters', agent_service_secret='worker-secret', deepgram_api_key='stt-test', openai_api_key='llm-test', eleven_api_key='tts-test', _env_file=None)


def catalog_transport(request):
    assert request.headers['xi-api-key'] == 'tts-test'
    assert request.url.params['include_custom_rates'] == 'false'
    voices = [{'voice_id':'arabic-voice','name':'Arabic voice','verified_languages':[{'language':'ar'}]}, {'voice_id':'other-voice','name':'Another voice'}]
    id = request.url.params.get('voice_ids')
    if id:
        voices = [voice for voice in voices if voice['voice_id'] == id]
    return httpx.Response(200,json={'voices':voices,'has_more':False})


def test_authenticated_catalog_search_and_safe_response(tmp_path):
    def handler(request):
        assert request.url.params['search'] == 'Arabic'
        return httpx.Response(200,json={'voices':[{'voice_id':'arabic-voice','name':'Arabic voice','samples':['do not expose']}], 'has_more':True,'next_page_token':'next'})
    service = ElevenLabsVoices('tts-test',transport=httpx.MockTransport(handler))
    with TestClient(create_app(configured(tmp_path),voice_catalog=service)) as client:
        assert client.get('/v1/voices').status_code == 401
        client.post('/v1/demo-session')
        response = client.get('/v1/voices?search=Arabic')
        assert response.json() == {'voices':[{'id':'arabic-voice','name':'Arabic voice','languages':[]}], 'next_page_token':'next'}
        assert 'do not expose' not in response.text
        assert 'tts-test' not in response.text


def test_unavailable_voice_never_reserves_or_dispatches_a_call(tmp_path):
    settings = configured(tmp_path)
    service = ElevenLabsVoices('tts-test',transport=httpx.MockTransport(catalog_transport))
    with TestClient(create_app(settings,voice_catalog=service)) as client:
        client.post('/v1/demo-session')
        chat = client.post('/v1/conversations').json()['id']
        for selection,code in [(None,'VOICE_REQUIRED'),('gone-voice','VOICE_UNAVAILABLE')]:
            response = client.post('/v1/voice/sessions',json={'conversation_id':chat,'voice_id':selection})
            assert response.status_code == 422
            assert response.json()['detail']['code'] == code
        with sqlite3.connect(tmp_path/'voice.db') as db:
            assert db.execute('SELECT COUNT(*) FROM voice_sessions').fetchone()[0] == 0


@pytest.fixture
def livekit_control_plane(monkeypatch):
    # Replace only the external LiveKit control-plane boundary. State and auth are real.
    from livekit import api
    class ControlPlane:
        def __init__(self,*args,**kwargs):
            self.agent_dispatch = SimpleNamespace(create_dispatch=self.dispatch)
            self.room = SimpleNamespace(delete_room=self.delete)
        async def dispatch(self,request):
            return None
        async def delete(self,request):
            return None
        async def __aenter__(self):
            return self
        async def __aexit__(self,*args):
            return None
    monkeypatch.setattr(api,'LiveKitAPI',ControlPlane)


def test_selected_voice_is_frozen_per_call_and_reaches_snapshot(tmp_path, livekit_control_plane):
    service = ElevenLabsVoices('tts-test',transport=httpx.MockTransport(catalog_transport))
    with TestClient(create_app(configured(tmp_path),voice_catalog=service)) as client:
        client.post('/v1/demo-session')
        chat = client.post('/v1/conversations').json()['id']
        def start(voice):
            return client.post('/v1/voice/sessions',json={'conversation_id':chat,'voice_id':voice})
        first = start('arabic-voice')
        assert first.status_code == 201
        session_id = first.json()['id']
        with sqlite3.connect(tmp_path/'voice.db') as db:
            room,pipeline = db.execute('SELECT room_name,pipeline FROM voice_sessions WHERE id=?',(session_id,)).fetchone()
        assert json.loads(pipeline)['tts']['voice_id'] == 'arabic-voice'
        assert start('other-voice').status_code == 429
        headers = {'x-agent-secret':'worker-secret','x-session-id':session_id,'x-room-name':room}
        snapshot = client.post('/internal/agent/turn/start',headers=headers,json={'request_id':'utterance','text':'Hello'}).json()['context']
        assert snapshot['pipeline_config']['tts']['voice_id'] == 'arabic-voice'
        assert client.post(f'/v1/voice/sessions/{session_id}/end').status_code == 200
        assert start('other-voice').status_code == 201


def test_start_voice_resolves_tier_from_preference_and_bootstrap_carries_guidance(tmp_path, livekit_control_plane):
    service = ElevenLabsVoices('tts-test',transport=httpx.MockTransport(catalog_transport))
    with TestClient(create_app(configured(tmp_path),voice_catalog=service)) as client:
        client.post('/v1/demo-session')
        chat = client.post('/v1/conversations').json()['id']

        def start(**extra):
            response = client.post('/v1/voice/sessions',json={'conversation_id':chat,'voice_id':'arabic-voice',**extra})
            assert response.status_code == 201
            session = response.json()
            with sqlite3.connect(tmp_path/'voice.db') as db:
                room,pipeline = db.execute('SELECT room_name,pipeline FROM voice_sessions WHERE id=?',(session['id'],)).fetchone()
            session.update(room=room,pipeline=json.loads(pipeline))
            return session

        def bootstrap(session):
            headers = {'x-agent-secret':'worker-secret','x-session-id':session['id'],'x-room-name':session['room']}
            return client.post('/internal/agent/bootstrap',headers=headers).json()

        # An explicit current-turn request overrides the saved preference.
        brief = start(response_tier=5,response_preference='brief')
        assert (brief['pipeline']['response_tier'],brief['pipeline']['max_output_tokens']) == (5,1024)
        assert client.post(f"/v1/voice/sessions/{brief['id']}/end").status_code == 200

        detailed = start(response_tier=4,response_preference='detailed')
        assert (detailed['pipeline']['response_tier'],detailed['pipeline']['max_output_tokens']) == (4,512)
        instructions = bootstrap(detailed)['instructions']
        assert 'about 180 words or fewer' in instructions
        assert '512' not in instructions and 'token' not in instructions.lower()


def test_provider_errors_are_typed_and_do_not_leak_response(tmp_path):
    service = ElevenLabsVoices('tts-test',transport=httpx.MockTransport(lambda request: httpx.Response(403,text='sensitive upstream detail')))
    with TestClient(create_app(configured(tmp_path),voice_catalog=service)) as client:
        client.post('/v1/demo-session')
        response = client.get('/v1/voices')
        assert response.status_code == 503
        assert response.json() == {'detail':{'code':'VOICE_CATALOG_ACCESS_DENIED'}}


def test_legacy_bootstrap_and_snapshot_share_effective_configuration(tmp_path, livekit_control_plane):
    service = ElevenLabsVoices('tts-test',transport=httpx.MockTransport(catalog_transport))
    with TestClient(create_app(configured(tmp_path),voice_catalog=service)) as client:
        client.post('/v1/demo-session')
        chat = client.post('/v1/conversations').json()['id']
        session = client.post('/v1/voice/sessions',json={'conversation_id':chat,'voice_id':'arabic-voice','response_tier':5,'response_preference':'brief'}).json()
        with sqlite3.connect(tmp_path/'voice.db') as db:
            room, raw = db.execute('SELECT room_name,pipeline FROM voice_sessions WHERE id=?',(session['id'],)).fetchone()
        headers = {'x-agent-secret':'worker-secret','x-session-id':session['id'],'x-room-name':room}
        for legacy in [False, True]:
            if legacy:
                source = json.loads(raw)
                source.pop('response_tier');source.pop('response_preference')
                source['max_output_tokens'] = 96;source['revision'] = 1
                with sqlite3.connect(tmp_path/'voice.db') as db:
                    db.execute('UPDATE voice_sessions SET pipeline=? WHERE id=?',(json.dumps(source),session['id']))
            bootstrap = client.post('/internal/agent/bootstrap',headers=headers).json()
            body = {'request_id':str(legacy),'text':'Hello'}
            first = client.post('/internal/agent/turn/start',headers=headers,json=body).json()
            repeat = client.post('/internal/agent/turn/start',headers=headers,json=body).json()
            assert first == repeat
            assert first['context']['pipeline_config'] == bootstrap['pipeline']
            assert bootstrap['pipeline']['max_output_tokens'] == (128 if legacy else 1024)
            assert first['context']['messages'][0]['content'] == bootstrap['instructions']
