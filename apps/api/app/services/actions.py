"""Authorized typed dispatch. Models propose actions; deterministic handlers own effects."""
import asyncio
import hashlib
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker

from app.domain.contracts import (
    AdjustSpeakingSpeed, SelectApprovedVoice, SetCaptionsVisible, SetSpeakingSpeed,
    ShowConversationHistory, OpenMemoryExplorer, UserSettings, apply_settings_action,
)
from app.infrastructure.database import ContextOperation, Conversation, SessionGate, SettingRevision, UserSetting, now

SETTING_ACTIONS = (AdjustSpeakingSpeed, SelectApprovedVoice, SetCaptionsVisible, SetSpeakingSpeed)


def settings_value(row):
    if row is None:
        return UserSettings()
    return UserSettings(speaking_speed=row.speaking_speed, captions_visible=row.captions_visible,
        approved_voice_id=row.approved_voice_id, response_preference=row.response_preference, revision=row.revision)


class EffectSession:
    """Narrow mutation capability; the dispatcher owns transaction lifecycle."""
    __slots__ = ('__session',)

    def __init__(self, session):
        self.__session = session

    async def execute(self, *args, **kwargs):
        return await self.__session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return await self.__session.scalar(*args, **kwargs)

    async def scalars(self, *args, **kwargs):
        return await self.__session.scalars(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return await self.__session.get(*args, **kwargs)

    def add(self, value):
        self.__session.add(value)

    def add_all(self, values):
        self.__session.add_all(values)

    async def flush(self):
        await self.__session.flush()

    async def delete(self, value):
        await self.__session.delete(value)

    async def commit(self):
        raise HTTPException(503, detail={'code': 'ACTION_TRANSACTION_OWNED'})

    async def rollback(self):
        raise HTTPException(503, detail={'code': 'ACTION_TRANSACTION_OWNED'})

    def begin(self):
        raise HTTPException(503, detail={'code': 'ACTION_TRANSACTION_OWNED'})


async def dispatch_action(db, *, owner_id, conversation_id, request_id, expected_revision, action, catalog, handlers):
    """Own a fresh short transaction, independent of authentication reads."""
    bind = db.bind
    factory = async_sessionmaker(bind.engine if isinstance(bind, AsyncConnection) else bind, expire_on_commit=False)
    key = hashlib.sha256(('dispatch:' + owner_id + ':' + request_id).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps({'conversation_id': conversation_id,
        'expected_revision': expected_revision, 'action': action.model_dump(mode='json')}, sort_keys=True).encode()).hexdigest()
    handler = handlers.get(action.kind)
    prepare = getattr(handler, 'prepare', None) if handler is not None else None
    if isinstance(action, SelectApprovedVoice):
        await catalog.require(action.voice_id)

    prepared = None
    preparation_id = None
    if prepare is not None:
        status, preparation_id, result = await _claim_preparation(
            factory, owner_id=owner_id, conversation_id=conversation_id, key=key, fingerprint=fingerprint)
        if status == 'replay':
            return result
        if status == 'in_progress':
            return await _wait_for_preparation(factory, key=key, owner_id=owner_id, fingerprint=fingerprint)
        try:
            async with factory() as read_db:
                prepared = await prepare(db=read_db, owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, expected_revision=expected_revision, action=action)
        except BaseException:
            await _delete_preparation(factory, preparation_id)
            raise
    try:
        async with factory() as effects:
            async with effects.begin():
                return await _dispatch_transaction(effects, owner_id=owner_id, conversation_id=conversation_id,
                    request_id=request_id, expected_revision=expected_revision, action=action, catalog=catalog, handlers=handlers,
                    prepared=prepared, prepared_operation_id=preparation_id)
    except BaseException:
        if preparation_id is not None:
            await _delete_preparation(factory, preparation_id)
        raise


async def _claim_preparation(factory, *, owner_id, conversation_id, key, fingerprint):
    async with factory() as claim_db:
        async with claim_db.begin():
            conversation = await claim_db.get(Conversation, conversation_id)
            if not conversation or conversation.owner_id != owner_id:
                raise HTTPException(404, detail={'code': 'CONVERSATION_NOT_FOUND'})
            if claim_db.bind.dialect.name == 'sqlite':
                from sqlalchemy.dialects.sqlite import insert
            elif claim_db.bind.dialect.name == 'postgresql':
                from sqlalchemy.dialects.postgresql import insert
            else:
                raise ValueError('Unsupported dispatch database')
            identifier = str(uuid4())
            values = dict(id=identifier, owner_id=owner_id, conversation_id=conversation_id,
                command_type='DispatchAction', payload={'fingerprint': fingerprint}, revision_before=None,
                revision_after=None, idempotency_key=key, outcome='preparing', created_at=now())
            result = await claim_db.execute(insert(ContextOperation).values(**values).on_conflict_do_nothing(
                index_elements=['idempotency_key']))
            if result.rowcount == 1:
                return 'winner', identifier, None
            existing = await claim_db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
            if existing is None:
                raise HTTPException(503, detail={'code': 'ACTION_REQUEST_UNKNOWN'})
            if existing.owner_id != owner_id or existing.payload.get('fingerprint') != fingerprint:
                raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT'})
            if existing.outcome == 'completed':
                return 'replay', None, existing.payload['result']
            if existing.outcome == 'preparing':
                return 'in_progress', existing.id, None
            await claim_db.delete(existing)
            await claim_db.flush()
            retry = await claim_db.execute(insert(ContextOperation).values(**values).on_conflict_do_nothing(
                index_elements=['idempotency_key']))
            if retry.rowcount == 1:
                return 'winner', identifier, None
            existing = await claim_db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
            if existing is None:
                raise HTTPException(503, detail={'code': 'ACTION_REQUEST_UNKNOWN'})
            if existing.owner_id != owner_id or existing.payload.get('fingerprint') != fingerprint:
                raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT'})
            if existing.outcome == 'completed':
                return 'replay', None, existing.payload['result']
            if existing.outcome == 'preparing':
                return 'in_progress', existing.id, None
            raise HTTPException(503, detail={'code': 'ACTION_REQUEST_FAILED'})


async def _wait_for_preparation(factory, *, key, owner_id, fingerprint):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        async with factory() as db:
            existing = await db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
            if existing is None:
                raise HTTPException(503, detail={'code': 'ACTION_REQUEST_UNKNOWN'})
            if existing.owner_id != owner_id or existing.payload.get('fingerprint') != fingerprint:
                raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT'})
            if existing.outcome == 'completed':
                return existing.payload['result']
            if existing.outcome in {'failed', 'unknown'}:
                raise HTTPException(503, detail={'code': 'ACTION_REQUEST_FAILED'})
    raise HTTPException(409, detail={'code': 'ACTION_IN_PROGRESS'})


async def _delete_preparation(factory, preparation_id):
    async with factory() as db:
        async with db.begin():
            await db.execute(delete(ContextOperation).where(
                ContextOperation.id == preparation_id, ContextOperation.outcome == 'preparing'))


async def _dispatch_transaction(db, *, owner_id, conversation_id, request_id, expected_revision, action, catalog, handlers, prepared=None, prepared_operation_id=None):
    # Existing gate serializes read-modify-write transactions on SQLite and PostgreSQL.
    await db.execute(update(SessionGate).where(SessionGate.id == 1).values(marker=1))
    conversation = await db.get(Conversation, conversation_id)
    if not conversation or conversation.owner_id != owner_id:
        raise HTTPException(404, detail={'code':'CONVERSATION_NOT_FOUND'})
    key = hashlib.sha256(('dispatch:' + owner_id + ':' + request_id).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps({'conversation_id':conversation_id,
        'expected_revision':expected_revision, 'action':action.model_dump(mode='json')},sort_keys=True).encode()).hexdigest()
    existing = await db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
    if existing:
        if existing.owner_id != owner_id or existing.payload.get('fingerprint') != fingerprint:
            raise HTTPException(409, detail={'code':'IDEMPOTENCY_CONFLICT'})
        if existing.outcome == 'completed':
            return existing.payload['result']
    if isinstance(action, SETTING_ACTIONS):
        row = await db.get(UserSetting, owner_id)
        before = settings_value(row)
        if before.revision != expected_revision:
            raise HTTPException(409, detail={'code':'SETTINGS_REVISION_CONFLICT'})
        after = apply_settings_action(before, action)
        if after.revision != before.revision:
            if row is None:
                row = UserSetting(owner_id=owner_id)
                db.add(row)
            for field in ('speaking_speed','captions_visible','approved_voice_id','revision'):
                setattr(row, field, getattr(after, field))
            row.response_preference = after.response_preference.value
            row.updated_at = now()
            db.add(SettingRevision(id=str(uuid4()),owner_id=owner_id,revision=after.revision,settings=after.model_dump(mode='json')))
        result = {'kind':action.kind,'settings':after.model_dump(mode='json'),'changed':after != before}
    elif isinstance(action, (ShowConversationHistory, OpenMemoryExplorer)):
        result = {'kind':action.kind,'navigation':'history' if isinstance(action,ShowConversationHistory) else 'memory_explorer'}
    else:
        handler = handlers.get(action.kind)
        if handler is None:
            raise HTTPException(503, detail={'code':'ACTION_HANDLER_UNAVAILABLE'})
        # Durable context/memory handlers must retain this transaction; the dispatcher commits once.
        prepare_args = {'prepared': prepared} if getattr(handler, 'prepare', None) is not None else {}
        result = await handler(db=EffectSession(db), owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id, expected_revision=expected_revision, action=action, **prepare_args)
    if prepared_operation_id is not None:
        operation = await db.get(ContextOperation, prepared_operation_id)
        if operation is not None:
            operation.outcome = 'completed'
            operation.payload = {'fingerprint': fingerprint, 'result': result}
        else:
            db.add(ContextOperation(id=str(uuid4()), owner_id=owner_id, conversation_id=conversation_id,
                command_type='DispatchAction', idempotency_key=key, outcome='completed',
                payload={'fingerprint': fingerprint, 'result': result}))
    else:
        db.add(ContextOperation(id=str(uuid4()), owner_id=owner_id, conversation_id=conversation_id,
            command_type='DispatchAction', idempotency_key=key, outcome='completed',
            payload={'fingerprint': fingerprint, 'result': result}))
    return result
