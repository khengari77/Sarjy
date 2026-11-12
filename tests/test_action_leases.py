"""Dispatcher preparation leases: recovery, fencing and client retry semantics."""
import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.domain.contracts import parse_action
from app.infrastructure.database import Base, ContextOperation, Conversation, Identity
from app.services.actions import _claim_preparation, _dispatch_transaction
from test_actions import chat, setup, submit


def identity(owner, request_id):
    return hashlib.sha256(('dispatch:' + owner + ':' + request_id).encode()).hexdigest()


def fingerprint(conversation, revision, action):
    return hashlib.sha256(json.dumps({'conversation_id': conversation, 'expected_revision': revision,
        'action': action.model_dump(mode='json')}, sort_keys=True).encode()).hexdigest()


def seed_claim(path, *, owner, conversation, request_id, action, prepared=None, lease='old', expired=True):
    key = identity(owner, request_id)
    payload = {'fingerprint': fingerprint(conversation, 0, action)}
    if prepared is not None:
        payload['prepared'] = prepared
    expires = (datetime.now(timezone.utc) + timedelta(seconds=-1 if expired else 600)).isoformat()
    with sqlite3.connect(path) as db:
        db.execute(
            'INSERT INTO context_operations (id,owner_id,conversation_id,command_type,payload,revision_before,'
            'revision_after,idempotency_key,outcome,lease_id,lease_expires_at,created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ('prep', owner, conversation, 'DispatchAction', json.dumps(payload), None, None,
             key, 'preparing', lease, expires, '2026-11-12T00:00:00+00:00'),
        )
        db.commit()
    return key


def install_handler(app, calls, prepared=None):
    async def prepare(**kwargs):
        calls.append('prepare')
        return prepared or {'proposal_id': 'fresh'}

    async def effect(**kwargs):
        calls.append('effect')
        return kwargs['prepared']

    effect.prepare = prepare
    app.state.action_handlers['ProposeContextExclusion'] = effect


def test_expired_claim_resumes_stored_preparation_without_repaying(tmp_path):
    app, path = setup(tmp_path)
    calls = []
    install_handler(app, calls, prepared={'proposal_id': 'cached'})
    action = parse_action({'kind': 'ProposeContextExclusion', 'query': 'trip'})
    with TestClient(app) as client:
        owner = client.post('/v1/demo-session').json()['id']
        conversation = chat(client)
        seed_claim(path, owner=owner, conversation=conversation, request_id='r1', action=action,
            prepared={'proposal_id': 'cached'})

        response = submit(client, conversation, source='ui', action={'kind': 'ProposeContextExclusion', 'query': 'trip'})

        assert response.status_code == 200, response.text
        assert response.json()['result'] == {'proposal_id': 'cached'}
        assert calls == ['effect']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT outcome FROM context_operations WHERE id=?', ('prep',)).fetchone()[0] == 'completed'


def test_expired_claim_without_preparation_reclaims_and_runs_once(tmp_path):
    app, path = setup(tmp_path)
    calls = []
    install_handler(app, calls)
    action = parse_action({'kind': 'ProposeContextExclusion', 'query': 'trip'})
    with TestClient(app) as client:
        owner = client.post('/v1/demo-session').json()['id']
        conversation = chat(client)
        seed_claim(path, owner=owner, conversation=conversation, request_id='r1', action=action)

        response = submit(client, conversation, source='ui', action={'kind': 'ProposeContextExclusion', 'query': 'trip'})

        assert response.status_code == 200, response.text
        assert calls == ['prepare', 'effect']
        assert submit(client, conversation, source='ui', action={'kind': 'ProposeContextExclusion', 'query': 'trip'}).json() == response.json()
        assert calls == ['prepare', 'effect']


def test_active_lease_keeps_duplicate_protection(tmp_path):
    app, path = setup(tmp_path)
    action = parse_action({'kind': 'ProposeContextExclusion', 'query': 'trip'})
    with TestClient(app) as client:
        owner = client.post('/v1/demo-session').json()['id']
        conversation = chat(client)
        seed_claim(path, owner=owner, conversation=conversation, request_id='r1', action=action, expired=False)

        async def run():
            engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                status, _, lease, _ = await _claim_preparation(
                    factory, owner_id=owner, conversation_id=conversation,
                    key=identity(owner, 'r1'), fingerprint=fingerprint(conversation, 0, action))
                assert status == 'in_progress' and lease == 'old'
            finally:
                await engine.dispose()
        asyncio.run(run())


def test_old_worker_cannot_commit_after_takeover(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/fenced.db')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    calls = []

    async def handler(**kwargs):
        calls.append('effect')
        return kwargs['prepared']

    async def run():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(Identity(id='owner', token_hash='h', expires_at='2030'))
            db.add(Conversation(id='chat', owner_id='owner', title='t', revision=0))
            await db.commit()
            db.add(ContextOperation(id='prep', owner_id='owner', conversation_id='chat',
                command_type='DispatchAction', payload={'fingerprint': 'f', 'prepared': {'ok': True}},
                idempotency_key='key', outcome='preparing', lease_id='new-lease',
                lease_expires_at='2030', created_at='2026-11-12T00:00:00+00:00'))
            await db.commit()
            action = parse_action({'kind': 'ProposeContextExclusion', 'query': 'trip'})
            with pytest.raises(HTTPException) as error:
                async with db.begin():
                    await _dispatch_transaction(db, owner_id='owner', conversation_id='chat', request_id='r1',
                        expected_revision=0, action=action, catalog=None, handlers={'ProposeContextExclusion': handler},
                        prepared={'ok': True}, prepared_operation_id='prep', preparation_lease='old-lease')
            assert error.value.detail['code'] == 'ACTION_LEASE_LOST'
            assert calls == []
        await engine.dispose()

    asyncio.run(run())


def test_takeover_during_effect_fails_completion_and_rolls_back(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/fenced-late.db')
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def handler(**kwargs):
        await kwargs['db'].execute(update(ContextOperation)
            .where(ContextOperation.id == 'prep').values(lease_id='new-lease'))
        return {'ok': True}

    async def run():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(Identity(id='owner', token_hash='h', expires_at='2030'))
            db.add(Conversation(id='chat', owner_id='owner', title='t', revision=0))
            await db.commit()
            db.add(ContextOperation(id='prep', owner_id='owner', conversation_id='chat',
                command_type='DispatchAction', payload={'fingerprint': 'f', 'prepared': {'ok': True}},
                idempotency_key='key', outcome='preparing', lease_id='old-lease',
                lease_expires_at='2030', created_at='2026-11-12T00:00:00+00:00'))
            await db.commit()
            action = parse_action({'kind': 'ProposeContextExclusion', 'query': 'trip'})
            with pytest.raises(HTTPException) as error:
                async with db.begin():
                    await _dispatch_transaction(db, owner_id='owner', conversation_id='chat', request_id='r1',
                        expected_revision=0, action=action, catalog=None, handlers={'ProposeContextExclusion': handler},
                        prepared={'ok': True}, prepared_operation_id='prep', preparation_lease='old-lease')
            assert error.value.detail['code'] == 'ACTION_LEASE_LOST'
        await engine.dispose()

    asyncio.run(run())
