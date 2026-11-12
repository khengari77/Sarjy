import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.context_operations import (
    BUDGET_PATTERN,
    MemoryCandidate,
    exclusion_diff,
    restore_diff,
    select_candidates,
    sign_proposal,
    verify_proposal,
)
from app.main import create_app


def candidate(record_id, content, kind='memory'):
    return MemoryCandidate(id=record_id, content=content, kind=kind)


def test_keep_exceptions_protect_budget_ids_and_terms():
    selection = select_candidates(
        (
            candidate('trip', 'Trip to Tripoli from 1 to 5 May'),
            candidate('hotel', 'Hotel in Rome, 3 nights'),
            candidate('budget', 'Budget 500 USD for the trip'),
            candidate('passport', 'Passport number X123'),
        ),
        keep=('passport',),
    )
    assert selection.excluded_ids == frozenset({'trip', 'hotel'})
    assert selection.kept_ids == frozenset({'budget', 'passport'})
    reasons = {record.id: record.reason for record in selection.preserved}
    assert reasons['budget'] == 'protected budget'
    assert reasons['passport'] == 'explicit keep id'


def test_budget_detection_covers_english_and_arabic():
    for text in ('Budget 500 USD', 'cost is $20', 'ميزانية الرحلة', 'تكلفة الفندق'):
        assert BUDGET_PATTERN.search(text)


def test_diffs_are_deterministic_and_sorted():
    assert exclusion_diff(frozenset({'b', 'a'}), frozenset({'c'}), 2, 3) == {
        'added': [],
        'removed': ['a', 'b'],
        'kept': ['c'],
        'revision_before': 2,
        'revision_after': 3,
    }
    assert restore_diff(frozenset({'a'}), frozenset(), 3, 4)['added'] == ['a']


def test_signed_proposal_round_trips_and_rejects_tampering():
    payload = {'owner_id': 'owner', 'conversation_id': 'chat', 'revision': 3, 'excluded_ids': ['a']}
    token = sign_proposal(payload, 'secret')
    assert verify_proposal(token, 'secret') == payload
    body, signature = token.rsplit('.', 1)
    tampered = f'{body[:-1]}A.{signature}'
    with pytest.raises(ValueError, match='PROPOSAL_INVALID'):
        verify_proposal(tampered, 'secret')
    with pytest.raises(ValueError, match='PROPOSAL_INVALID'):
        verify_proposal(token, 'wrong-secret')


def configured(tmp_path):
    file = tmp_path / 'context.db'
    return file, Settings(
        database_url=f'sqlite+aiosqlite:///{file}',
        agent_service_secret='test-service-secret',
        context_proposal_secret='proposal-secret',
        _env_file=None,
    )


def seed(file, chat, rows=(
    ('trip', 'Trip to Tripoli from 1 to 5 May'),
    ('hotel', 'Hotel in Rome, 3 nights'),
    ('budget', 'Budget 500 USD for the trip'),
)):
    with sqlite3.connect(file) as db:
        for record_id, content in rows:
            db.execute(
                'INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,created_at,updated_at) '
                "VALUES (?, (SELECT owner_id FROM conversations WHERE id=?), ?, 'text-embedding-3-small', ?, 1, 'active', "
                "'2026-11-12T00:00:00+00:00', '2026-11-12T00:00:00+00:00')",
                (record_id, chat, content, chat),
            )
        db.commit()


def counts(file):
    with sqlite3.connect(file) as db:
        revision = db.execute('SELECT revision FROM conversations').fetchone()[0]
        exclusions = db.execute('SELECT count(*) FROM context_exclusions').fetchone()[0]
        active = db.execute('SELECT count(*) FROM context_exclusions WHERE active=1').fetchone()[0]
        operations = db.execute('SELECT count(*) FROM context_operations').fetchone()[0]
    return revision, exclusions, active, operations


def test_preview_confirm_duplicate_stale_and_undo(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post('/v1/demo-session')
        chat = client.post('/v1/conversations').json()['id']
        seed(file, chat)

        preview = client.post(f'/v1/conversations/{chat}/context/proposals', json={'query': 'forget the trip hotel'})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body['revision'] == 0
        assert body['diff']['revision_before'] == body['diff']['revision_after'] == 0
        assert {item['id'] for item in body['preserved']} == {'budget'}
        assert {item['id'] for item in body['matches']} >= {'trip', 'hotel', 'budget'}
        assert counts(file) == (0, 0, 0, 0), 'preview must not mutate persistence'

        confirm = client.post(
            f'/v1/conversations/{chat}/context/confirm',
            json={'proposal': body['proposal'], 'expected_revision': 0, 'idempotency_key': 'confirm-1'},
        )
        assert confirm.status_code == 200, confirm.text
        applied = confirm.json()
        assert applied['idempotent'] is False
        assert set(applied['excluded_ids']) == {'trip', 'hotel'}
        assert applied['kept_ids'] == ['budget']
        assert applied['revision_before'] == 0 and applied['revision_after'] == 1
        assert counts(file) == (1, 2, 2, 1)

        replay = client.post(
            f'/v1/conversations/{chat}/context/confirm',
            json={'proposal': body['proposal'], 'expected_revision': 0, 'idempotency_key': 'confirm-1'},
        )
        assert replay.json()['idempotent'] is True
        assert replay.json()['operation_id'] == applied['operation_id']
        assert counts(file) == (1, 2, 2, 1), 'duplicate confirmation must not add exclusions or revisions'

        stale = client.post(
            f'/v1/conversations/{chat}/context/confirm',
            json={'proposal': body['proposal'], 'expected_revision': 0, 'idempotency_key': 'confirm-stale'},
        )
        assert stale.status_code == 409
        assert stale.json()['detail']['code'] == 'REVISION_CONFLICT'
        assert counts(file) == (1, 2, 2, 1)

        state = client.get(f'/v1/conversations/{chat}/context').json()
        assert state['revision'] == 1
        assert state['excluded_ids'] == ['hotel', 'trip']

        undo = client.post(
            f'/v1/conversations/{chat}/context/undo',
            json={'operation_id': applied['operation_id'], 'expected_revision': 1, 'idempotency_key': 'undo-1'},
        )
        assert undo.status_code == 200, undo.text
        restored = undo.json()
        assert restored['restored_ids'] == ['hotel', 'trip']
        assert restored['revision_before'] == 1 and restored['revision_after'] == 2
        assert counts(file) == (2, 2, 0, 2)
        assert client.get(f'/v1/conversations/{chat}/context').json()['excluded_ids'] == []

        undo_replay = client.post(
            f'/v1/conversations/{chat}/context/undo',
            json={'operation_id': applied['operation_id'], 'expected_revision': 1, 'idempotency_key': 'undo-1'},
        )
        assert undo_replay.json()['idempotent'] is True
        assert counts(file) == (2, 2, 0, 2)


def test_tenant_isolation_and_bad_proposals(tmp_path):
    file, settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post('/v1/demo-session')
        chat = client.post('/v1/conversations').json()['id']
        seed(file, chat)
        proposal = client.post(
            f'/v1/conversations/{chat}/context/proposals',
            json={'query': 'forget the trip hotel'},
        ).json()

    with TestClient(create_app(settings)) as other:
        other.post('/v1/demo-session')
        assert other.get(f'/v1/conversations/{chat}/context').status_code == 404
        assert other.post(f'/v1/conversations/{chat}/context/proposals', json={'query': 'trip'}).status_code == 404
        forged = other.post(
            f'/v1/conversations/{chat}/context/confirm',
            json={'proposal': proposal['proposal'], 'expected_revision': 0, 'idempotency_key': 'forged'},
        )
        assert forged.status_code == 404
    assert counts(file) == (0, 0, 0, 0)
