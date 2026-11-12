"""Independent Day 2 integration verification.

This file is deliberately test-only. Cases whose implementation is still on an
unmerged branch (or owned by #5) are skipped with a named blocker instead of
being replaced by a passing mock; re-run them once the dependency lands.
"""
import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st
from pydantic import ValidationError

import app.main as main_module
from app.config import Settings
from app.domain.contracts import (
    CONFIDENCE_LEAD,
    CONFIDENCE_THRESHOLD,
    Decision,
    DecisionSource,
    Complexity,
    Intent,
    ResponsePolicy,
    ResponsePreference,
    resolve_response_policy,
)
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
POSTGRES_URL = os.environ.get('TEST_POSTGRES_URL', '')
HAS_CONTEXT_ASSEMBLY = importlib.util.find_spec('app.domain.weather_permissions') is not None
HAS_CONTEXT_OPERATIONS = importlib.util.find_spec('app.services.context_operations') is not None


def distribution(selected=Intent.CONVERSATION, top=0.55):
    """A valid eight-outcome distribution with `selected` as the maximum."""
    others = [intent for intent in Intent if intent is not selected]
    remainder = (1.0 - top) / len(others)
    return {selected: top, **{intent: remainder for intent in others}}


def decision(**overrides):
    values = {
        'turn_id': 'turn-1',
        'intent': Intent.CONVERSATION,
        'probabilities': distribution(),
        'confidence': 0.99,
        'tier': 2,
        'complexity': Complexity.SIMPLE,
        'source': DecisionSource.JEV,
        'model': 'gpt-6-luna',
        'policy_version': '2026-11-12',
    }
    values.update(overrides)
    return Decision(**values)


def test_routing_uses_selected_probability_and_lead_not_vendor_confidence():
    routed = decision(confidence=0.99, probabilities=distribution(top=0.55))
    assert routed.selected_probability == pytest.approx(0.55)
    assert routed.lead == pytest.approx(0.55 - (0.45 / 7))
    assert routed.permits_automatic_action(threshold=0.5, lead=0.4)
    assert not routed.permits_automatic_action(threshold=0.6)
    # Vendor confidence above the threshold cannot rescue a weak probability.
    weak_probabilities = {Intent.CONVERSATION: 0.42, Intent.TOOL_REQUEST: 0.35}
    remaining = [intent for intent in Intent if intent not in weak_probabilities]
    weak_probabilities.update({intent: (1 - 0.42 - 0.35) / len(remaining) for intent in remaining})
    weak = decision(confidence=1.0, probabilities=weak_probabilities)
    assert weak.lead < CONFIDENCE_LEAD
    assert not weak.permits_automatic_action(threshold=0.4, lead=0.4)


def test_declared_intent_must_be_the_maximum_and_probabilities_must_sum_to_one():
    with pytest.raises(ValidationError, match='SELECTED_INTENT_NOT_MAXIMUM'):
        decision(intent=Intent.TOOL_REQUEST, probabilities=distribution(Intent.CONVERSATION))
    with pytest.raises(ValidationError, match='PROBABILITY_SUM'):
        decision(probabilities={intent: 0.5 for intent in Intent})


def test_preference_bounds_classified_tiers_but_explicit_overrides():
    assert resolve_response_policy(ResponsePreference.BRIEF, classified_tier=5).tier == 2
    assert resolve_response_policy(ResponsePreference.BALANCED, classified_tier=5).tier == 3
    assert resolve_response_policy(ResponsePreference.BRIEF, explicit_tier=5).max_output_tokens == 1024
    with pytest.raises(ValidationError):
        resolve_response_policy(ResponsePreference.BRIEF, explicit_tier=6)


def test_tier_caps_are_exact_and_tier_bound():
    assert tuple(ResponsePolicy.for_tier(tier).max_output_tokens for tier in range(1, 6)) == (64, 128, 256, 512, 1024)
    with pytest.raises(ValidationError):
        ResponsePolicy.for_tier(0)


def test_migration_chain_has_one_head_and_expected_parent():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'migrations'))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ['0005_day2_context']
    assert script.get_revision('0005_day2_context').down_revision == '0004_voice_cleanup'


@given(st.lists(st.text(min_size=1, max_size=40), min_size=1, max_size=20))
def test_legacy_context_hash_is_input_order_deterministic(contents):
    from app.domain.context import ContextRecord, compile_context

    records = tuple(ContextRecord(str(index), 'user', text) for index, text in enumerate(contents))
    first = compile_context(records, 4)
    second = compile_context(tuple(records), 4)
    assert first == second
    assert first['revision'] == 4


def configured(tmp_path):
    file = tmp_path / 'integration.db'
    return file, Settings(
        database_url=f'sqlite+aiosqlite:///{file}',
        agent_service_secret='test-service-secret',
        context_proposal_secret='proposal-secret',
        _env_file=None,
    )


def add_turn_session(client, file, chat, owner):
    with sqlite3.connect(file) as db:
        db.execute(
            'INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)',
            ('session', chat, owner, 'room', 'active', (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), json.dumps({})),
        )
        db.commit()
    return {'x-agent-secret': 'test-service-secret', 'x-session-id': 'session', 'x-room-name': 'room'}


def test_duplicate_turn_request_does_not_create_duplicate_turns(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner = client.post('/v1/demo-session').json()['id']
        chat = client.post('/v1/conversations').json()['id']
        headers = add_turn_session(client, file, chat, owner)
        body = {'request_id': 'same-request', 'text': 'Repeat me once'}
        first = client.post('/internal/agent/turn/start', headers=headers, json=body)
        second = client.post('/internal/agent/turn/start', headers=headers, json=body)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        turns = client.get(f'/v1/conversations/{chat}/turns').json()
        assert [turn['text'] for turn in turns] == ['Repeat me once']


def test_tenant_isolation_for_conversation_reads(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post('/v1/demo-session')
        chat = client.post('/v1/conversations').json()['id']
        client.post('/v1/conversations')
    with TestClient(create_app(settings)) as other:
        other.post('/v1/demo-session')
        assert other.get(f'/v1/conversations/{chat}/turns').status_code == 404
        assert other.get(f'/v1/conversations/{chat}/tool-calls').status_code == 404


def test_budget_denial_is_reported_before_any_provider_work(tmp_path, monkeypatch):
    _, settings = configured(tmp_path)
    calls = []

    async def fake_weather(city, country=None):
        calls.append(city)
        return {'city': city}

    monkeypatch.setattr(main_module, 'get_current_weather', fake_weather)
    with TestClient(create_app(settings)) as client:
        headers = {'x-agent-secret': 'test-service-secret'}
        first = client.post('/internal/development/reserve', headers=headers, json={'provider': 'elevenlabs', 'characters': 500})
        second = client.post('/internal/development/reserve', headers=headers, json={'provider': 'elevenlabs', 'characters': 500})
        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()['detail']['code'] == 'DAILY_BUDGET_EXHAUSTED'
        assert calls == []


@pytest.mark.skipif(not HAS_CONTEXT_ASSEMBLY, reason='blocked on PR #34 / issue #6 (weather permissions not merged)')
def test_weather_cannot_use_an_excluded_location_memory(tmp_path, monkeypatch):
    file, settings = configured(tmp_path)
    calls = []

    async def fake_weather(city, country=None):
        calls.append(city)
        return {'city': city}

    monkeypatch.setattr(main_module, 'get_current_weather', fake_weather)
    with TestClient(create_app(settings)) as client:
        owner = client.post('/v1/demo-session').json()['id']
        chat = client.post('/v1/conversations').json()['id']
        with sqlite3.connect(file) as db:
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,created_at,updated_at) "
                "VALUES ('trip', ?, 'Trip to Tripoli', 'text-embedding-3-small', ?, 1, 'active', "
                "'2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
                (owner, chat),
            )
            db.execute(
                "INSERT INTO context_operations (id,owner_id,conversation_id,command_type,payload,revision_before,revision_after,idempotency_key,outcome,created_at) "
                "VALUES ('op-seed', ?, ?, 'seed', '{}', 0, 0, 'seed-key', 'applied', '2026-11-12T00:00:00+00:00')",
                (owner, chat),
            )
            db.execute(
                "INSERT INTO context_exclusions (id,owner_id,conversation_id,memory_id,operation_id,scope,active,created_at) "
                "VALUES ('ex-trip', ?, ?, 'trip', 'op-seed', 'conversation', 1, '2026-11-12T00:00:00+00:00')",
                (owner, chat),
            )
            db.commit()
        headers = add_turn_session(client, file, chat, owner)
        turn = client.post('/internal/agent/turn/start', headers=headers, json={'request_id': 'weather-1', 'text': 'What is the weather?'}).json()
        response = client.post(
            '/internal/agent/tool/weather',
            headers=headers,
            json={'turn_id': turn['turn_id'], 'request_id': 'w1', 'city': 'Tripoli'},
        )
        if 'weather_policy' in turn['context']:
            assert response.json() == {'error': 'LOCATION_EXCLUDED'}
            assert calls == []


@pytest.mark.skipif(not HAS_CONTEXT_OPERATIONS, reason='blocked on PR #35 / issue #8 (proposal service not merged)')
def test_trip_exclusion_preserves_budget_and_undo_restores_exactly(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        owner = client.post('/v1/demo-session').json()['id']
        chat = client.post('/v1/conversations').json()['id']
        with sqlite3.connect(file) as db:
            for record_id, content in (
                ('trip', 'Trip to Tripoli from 1 to 5 May'),
                ('hotel', 'Hotel in Rome, 3 nights'),
                ('budget', 'Budget 500 USD for the trip'),
            ):
                db.execute(
                    "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,created_at,updated_at) "
                    "VALUES (?, ?, ?, 'text-embedding-3-small', ?, 1, 'active', '2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
                    (record_id, owner, content, chat),
                )
            db.commit()
        preview = client.post(f'/v1/conversations/{chat}/context/proposals', json={'query': 'forget the trip hotel'}).json()
        assert {item['id'] for item in preview['preserved']} == {'budget'}
        with sqlite3.connect(file) as db:
            assert db.execute('SELECT count(*) FROM context_exclusions').fetchone()[0] == 0
        applied = client.post(
            f'/v1/conversations/{chat}/context/confirm',
            json={'proposal': preview['proposal'], 'expected_revision': 0, 'idempotency_key': 'c1'},
        ).json()
        assert set(applied['excluded_ids']) == {'trip', 'hotel'}
        with sqlite3.connect(file) as db:
            assert db.execute('SELECT count(*) FROM context_exclusions WHERE active=1').fetchone()[0] == 2
        restored = client.post(
            f'/v1/conversations/{chat}/context/undo',
            json={'operation_id': applied['operation_id'], 'expected_revision': 1, 'idempotency_key': 'u1'},
        ).json()
        assert set(restored['restored_ids']) == {'trip', 'hotel'}
        with sqlite3.connect(file) as db:
            assert db.execute('SELECT count(*) FROM context_exclusions WHERE active=1').fetchone()[0] == 0


@pytest.mark.skip(reason='blocked on #5 memory repository (cross-session recall and correction versions not merged)')
def test_cross_session_recall_and_correction_versions():
    """Belongs here once #5 lands: seed a fact in conversation A, reopen in a
    fresh process/connection from conversation B, correct it and assert only the
    active version with source/provenance is returned."""


@pytest.mark.skip(reason='blocked on #5 memory repository (real pgvector retrieval not merged)')
def test_real_pgvector_retrieval_uses_owner_scope():
    """Belongs here once #5 lands: insert embeddings in a disposable pgvector
    database, query nearest neighbours and assert tenant isolation."""


@pytest.mark.skipif(not POSTGRES_URL, reason='TEST_POSTGRES_URL is not set (disposable PostgreSQL only)')
def test_disposable_postgres_migrations_reach_the_day2_head():
    import asyncio
    import subprocess

    env = os.environ | {'DATABASE_URL': POSTGRES_URL}
    result = subprocess.run(['.venv/bin/alembic', 'heads'], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '0005_day2_context' in result.stdout
