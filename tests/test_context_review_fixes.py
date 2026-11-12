import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.domain.context import compile_turn_context, validate_assembly
from app.domain.weather_permissions import LocationEvidence, decide_weather_request
from app.main import create_app
from test_context_assembly import configured, seed_memories


def test_compilation_detaches_nested_configuration_and_validates_actual_messages():
    original = {'llm': {'provider': 'openai_direct', 'model': 'gpt-6-luna'}, 'extra': [{'x': 1}]}
    context = compile_turn_context(revision=0, instructions='Rules', pipeline=original)
    original['llm']['model'] = 'different'
    assert context.pipeline_config['llm']['model'] == 'gpt-6-luna'
    with pytest.raises(TypeError):
        context.pipeline_config['extra'][0]['x'] = 2
    snapshot = context.to_snapshot()
    validate_assembly(0, context.manifest, snapshot)
    snapshot['messages'][0]['content'] = 'Changed rules'
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        validate_assembly(0, context.manifest, snapshot)
    exported = context.to_snapshot()
    exported['pipeline_config']['llm']['model'] = 'changed'
    assert context.pipeline_config['llm']['model'] == 'gpt-6-luna'
    with pytest.raises(ValueError, match='CONFIG_MISMATCH'):
        validate_assembly(0, context.manifest, exported)


def test_qualified_city_and_unknown_legacy_evidence_fail_closed():
    blocked = (LocationEvidence('trip', 'Trip to Tripoli'),)
    for city in ['Tripoli', 'Tripoli, Libya']:
        decision = decide_weather_request(city=city, country='LY', user_text='What is the weather?', excluded=blocked)
        assert not decision.allowed and decision.reason == 'LOCATION_EXCLUDED'
    assert not decide_weather_request(city='Tripoli', country='LY', user_text='What is the weather?').allowed
    assert decide_weather_request(city='Tripoli, Libya', country='LY', user_text='Weather in Tripoli?').allowed


def test_real_reader_filters_tracked_sources_derivatives_and_unknown_tools_but_keeps_independent_history(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner = client.post('/v1/demo-session').json()['id']
        chat = client.post('/v1/conversations').json()['id']
        seed_memories(file, chat, [('m-city', 'I live in Paris', 'active'), ('old', 'I lived in Rome', 'superseded')], ['m-city'])
        with sqlite3.connect(file) as db:
            def turn(identifier, role, text, request, snapshot=None):
                db.execute('INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) VALUES (?,?,?,?,?,?,?,?)',
                    (identifier, chat, request, role, text, 'completed', '2026-11-12T00:00:00+00:00', json.dumps(snapshot) if snapshot else None))
            turn('source', 'user', 'I live in Paris', 'source')
            turn('independent', 'user', 'I independently mentioned Paris', 'independent')
            turn('weather-user', 'user', 'What is the weather?', 'weather', {'included_record_ids': ['m-city']})
            turn('assistant', 'assistant', 'Paris is 20 degrees', 'reply:weather-user')
            turn('legacy-user', 'user', 'Another question', 'legacy')
            db.execute('UPDATE memories SET source_turn_id=? WHERE id=?', ('source', 'm-city'))
            for identifier, parent in [('derived', 'weather-user'), ('unknown', 'legacy-user')]:
                db.execute('INSERT INTO tool_calls (id,turn_id,request_id,name,result) VALUES (?,?,?,?,?)',
                    (identifier, parent, identifier, 'get_current_weather', json.dumps({'city': 'Paris'})))
            db.execute('INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)',
                ('session', chat, owner, 'room', 'active', (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(), '{}'))
        headers = {'x-agent-secret': 'test-service-secret', 'x-session-id': 'session', 'x-room-name': 'room'}
        response = client.post('/internal/agent/turn/start', headers=headers, json={'request_id': 'next', 'text': 'What do you remember?'})
        assert response.status_code == 200, response.text
        context = response.json()['context']
        ids = set(context['included_record_ids'])
        assert 'independent' in ids and 'weather-user' in ids
        assert not ids & {'m-city', 'source', 'assistant', 'derived', 'unknown'}
        assert context['weather_policy']['excluded_memory_ids'] == ['m-city']
        assert 'old' not in context['weather_policy']['excluded_memory_ids']
        with sqlite3.connect(file) as db:
            db.execute('UPDATE context_exclusions SET active=0')
        restored = client.post('/internal/agent/turn/start', headers=headers, json={'request_id': 'restored', 'text': 'Next'}).json()['context']
        assert {'m-city', 'source', 'assistant', 'derived'} <= set(restored['included_record_ids'])
