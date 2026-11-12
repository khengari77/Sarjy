"""Authorized typed dispatch. Models propose actions; deterministic handlers own effects."""
import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

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


async def dispatch_action(db, *, owner_id, conversation_id, request_id, expected_revision, action, catalog, handlers):
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
        return existing.payload['result']
    if isinstance(action, SETTING_ACTIONS):
        row = await db.get(UserSetting, owner_id)
        before = settings_value(row)
        if before.revision != expected_revision:
            raise HTTPException(409, detail={'code':'SETTINGS_REVISION_CONFLICT'})
        if isinstance(action, SelectApprovedVoice):
            # Catalog validation is metadata-only; no synthesis or inference occurs.
            await catalog.require(action.voice_id)
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
        result = await handler(db=db, owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id, expected_revision=expected_revision, action=action)
    db.add(ContextOperation(id=str(uuid4()),owner_id=owner_id,conversation_id=conversation_id,
        command_type='DispatchAction',idempotency_key=key,outcome='completed',
        payload={'fingerprint':fingerprint,'result':result}))
    await db.commit()
    return result
