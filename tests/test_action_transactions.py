import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.domain.contracts import SetCaptionsVisible
from app.infrastructure.database import Conversation
from app.services.actions import dispatch_action
from app.services.voices import VoiceOption
from test_actions import setup, chat, submit, Catalog


@pytest.mark.parametrize('commit_attempt',[False,True])
def test_handler_cannot_persist_effects_without_dispatch_ledger(tmp_path,commit_attempt):
    app,path=setup(tmp_path)
    async def faulty(**kwargs):
        db=kwargs['db']
        await db.execute(update(Conversation).where(Conversation.id==kwargs['conversation_id']).values(title='uncommitted'))
        await db.flush()
        if commit_attempt:
            await db.commit()
        raise HTTPException(409,detail={'code':'TEST_FAILURE'})
    app.state.action_handlers['ProposeContextExclusion']=faulty
    with TestClient(app) as client:
        conversation=chat(client)
        result=submit(client,conversation,source='ui',action={'kind':'ProposeContextExclusion','query':'trip'})
        assert result.status_code==(503 if commit_attempt else 409)
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT title FROM conversations WHERE id=?',(conversation,)).fetchone()[0]!='uncommitted'
            assert db.execute('SELECT COUNT(*) FROM context_operations').fetchone()[0]==0


def test_replay_does_not_leave_owned_transaction_open(tmp_path):
    app,path=setup(tmp_path)
    with TestClient(app) as client:
        conversation=chat(client)
        owner=client.post('/v1/demo-session').json()['id']
    async def run():
        engine=create_async_engine(f'sqlite+aiosqlite:///{path}')
        factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with factory() as db:
                values=dict(owner_id=owner,conversation_id=conversation,request_id='same',expected_revision=0,
                    action=SetCaptionsVisible(visible=False),catalog=None,handlers={})
                first=await dispatch_action(db,**values)
                assert not db.in_transaction()
                assert await dispatch_action(db,**values)==first
                assert not db.in_transaction()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_slow_catalog_does_not_lock_unrelated_mutations(tmp_path,monkeypatch):
    entered,release=threading.Event(),threading.Event()
    async def stalled(self,voice_id):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return VoiceOption(id=voice_id,name='Approved')
    monkeypatch.setattr(Catalog,'require',stalled)
    app,path=setup(tmp_path)
    with TestClient(app) as alice:
        bob=TestClient(app)
        alice_chat,bob_chat=chat(alice),chat(bob)
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(submit,alice,alice_chat,source='ui',action={'kind':'SelectApprovedVoice','voice_id':'approved'})
            try:
                assert entered.wait(5)
                second=pool.submit(submit,bob,bob_chat,source='ui',action={'kind':'SetCaptionsVisible','visible':False})
                assert second.result(timeout=3).status_code==200
            finally:
                release.set()
            assert first.result(timeout=5).status_code==200


def test_openapi_exports_typed_actions_and_response(tmp_path):
    app,path=setup(tmp_path)
    schema=app.openapi()
    action_schema=schema['components']['schemas']['ActionSubmission']['properties']['action']
    assert 'discriminator' in str(action_schema) and 'SetSpeakingSpeed' in str(action_schema)
    response=schema['paths']['/v1/actions']['post']['responses']['200']['content']['application/json']['schema']
    assert response['discriminator']['propertyName']=='handled'
    assert 'SettingsActionResult' in schema['components']['schemas']
    assert 'NavigationActionResult' in schema['components']['schemas']


def test_handler_prepare_runs_once_outside_effect_transaction_and_replay(tmp_path):
    app,path=setup(tmp_path)
    calls=[]
    async def prepare(**kwargs):
        assert not kwargs['db'].in_transaction()
        calls.append('prepare')
        return {'proposal_id':'detached-preview'}
    async def handler(**kwargs):
        calls.append('effect')
        assert kwargs['prepared']=={'proposal_id':'detached-preview'}
        return kwargs['prepared']
    handler.prepare=prepare
    app.state.action_handlers['ProposeContextExclusion']=handler
    with TestClient(app) as client:
        conversation=chat(client)
        values=dict(source='ui',action={'kind':'ProposeContextExclusion','query':'trip'})
        first=submit(client,conversation,**values)
        assert first.status_code==200
        assert submit(client,conversation,**values).json()==first.json()
        assert calls==['prepare','effect']
