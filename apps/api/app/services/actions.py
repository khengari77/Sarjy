"""Authorized typed dispatch. Models propose actions; deterministic handlers own effects."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker

from app.domain.contracts import (
    AdjustSpeakingSpeed,
    OpenMemoryExplorer,
    SelectApprovedVoice,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    ShowConversationHistory,
    UserSettings,
    apply_settings_action,
)
from app.infrastructure.database import (
    ContextOperation,
    Conversation,
    SessionGate,
    SettingRevision,
    UserSetting,
    VoiceControl,
    VoiceSession,
    now,
)
from app.infrastructure.upsert import insert_ignore
from app.services.voice_control import control_expiry, voice_state

SETTING_ACTIONS = (AdjustSpeakingSpeed, SelectApprovedVoice, SetCaptionsVisible, SetSpeakingSpeed)
PREPARATION_LEASE_SECONDS = 120
PREPARED_MAX_BYTES = 64 * 1024


def _lease_expiry() -> str:
    return (datetime.now(UTC) + timedelta(seconds=PREPARATION_LEASE_SECONDS)).isoformat()


def _lease_expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.now(UTC)
    except ValueError:
        return True


def settings_value(row):
    if row is None:
        return UserSettings()
    return UserSettings(
        speaking_speed=row.speaking_speed,
        captions_visible=row.captions_visible,
        approved_voice_id=row.approved_voice_id,
        response_preference=row.response_preference,
        revision=row.revision,
    )


def _replay_result(existing):
    result = (existing.payload or {}).get("result")
    if result is None:
        raise HTTPException(503, detail={"code": "ACTION_REQUEST_UNKNOWN"})
    return result


class EffectSession:
    """Narrow mutation capability; the dispatcher owns transaction lifecycle."""

    __slots__ = ("__session",)

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
        raise HTTPException(503, detail={"code": "ACTION_TRANSACTION_OWNED"})

    async def rollback(self):
        raise HTTPException(503, detail={"code": "ACTION_TRANSACTION_OWNED"})

    def begin(self):
        raise HTTPException(503, detail={"code": "ACTION_TRANSACTION_OWNED"})


async def dispatch_action(db, *, owner_id, conversation_id, request_id, expected_revision, action, catalog, handlers):
    """Own a fresh short transaction, independent of authentication reads."""
    bind = db.bind
    factory = async_sessionmaker(bind.engine if isinstance(bind, AsyncConnection) else bind, expire_on_commit=False)
    key = _dispatch_key(owner_id, request_id)
    fingerprint = _dispatch_fingerprint(conversation_id, expected_revision, action)
    handler = handlers.get(action.kind)
    prepare = getattr(handler, "prepare", None) if handler is not None else None
    if isinstance(action, SelectApprovedVoice):
        await catalog.require(action.voice_id)

    prepared = None
    preparation_id = None
    preparation_lease = None
    if prepare is not None:
        status, preparation_id, preparation_lease, result = await _claim_preparation(
            factory, owner_id=owner_id, conversation_id=conversation_id, key=key, fingerprint=fingerprint
        )
        if status == "replay":
            return result
        if status == "in_progress":
            return await _wait_for_preparation(factory, key=key, owner_id=owner_id, fingerprint=fingerprint)
        if status == "resume":
            prepared = result
        else:
            try:
                async with factory() as read_db:
                    prepared = await prepare(
                        db=read_db,
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        request_id=request_id,
                        expected_revision=expected_revision,
                        action=action,
                    )
                await _store_prepared(factory, preparation_id, preparation_lease, prepared)
            except BaseException:
                await _delete_preparation(factory, preparation_id, preparation_lease)
                raise
    try:
        async with factory() as effects, effects.begin():
            return await _dispatch_transaction(
                effects,
                owner_id=owner_id,
                conversation_id=conversation_id,
                request_id=request_id,
                expected_revision=expected_revision,
                action=action,
                catalog=catalog,
                handlers=handlers,
                prepared=prepared,
                prepared_operation_id=preparation_id,
                preparation_lease=preparation_lease,
            )
    except BaseException:
        if preparation_id is not None:
            await _delete_preparation(factory, preparation_id, preparation_lease)
        raise


def _preparation_values(owner_id, conversation_id, key, fingerprint, identifier, lease_id):
    return dict(
        id=identifier,
        owner_id=owner_id,
        conversation_id=conversation_id,
        command_type="DispatchAction",
        payload={"fingerprint": fingerprint},
        revision_before=None,
        revision_after=None,
        idempotency_key=key,
        outcome="preparing",
        lease_id=lease_id,
        lease_expires_at=_lease_expiry(),
        created_at=now(),
    )


def _existing_claim_outcome(existing, owner_id, fingerprint):
    if existing is None:
        raise HTTPException(503, detail={"code": "ACTION_REQUEST_UNKNOWN"})
    if existing.owner_id != owner_id or (existing.payload or {}).get("fingerprint") != fingerprint:
        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
    if existing.outcome == "completed":
        return "replay", None, None, _replay_result(existing)
    if existing.outcome == "preparing" and not _lease_expired(existing.lease_expires_at):
        return "in_progress", existing.id, existing.lease_id, None
    return None


async def _recover_expired_claim(claim_db, existing, owner_id, conversation_id, key, fingerprint):
    prepared = (existing.payload or {}).get("prepared")
    if prepared is not None:
        new_lease = str(uuid4())
        changed = await claim_db.execute(
            update(ContextOperation)
            .where(
                ContextOperation.id == existing.id,
                ContextOperation.outcome == "preparing",
                ContextOperation.lease_id == existing.lease_id,
            )
            .values(lease_id=new_lease, lease_expires_at=_lease_expiry())
        )
        if changed.rowcount == 1:
            return "resume", existing.id, new_lease, prepared
        return "in_progress", existing.id, existing.lease_id, None
    removed = await claim_db.execute(
        delete(ContextOperation).where(
            ContextOperation.id == existing.id,
            ContextOperation.outcome == "preparing",
            ContextOperation.lease_id == existing.lease_id,
        )
    )
    if removed.rowcount != 1:
        return "in_progress", existing.id, existing.lease_id, None
    identifier, lease_id = str(uuid4()), str(uuid4())
    retry = await insert_ignore(
        claim_db,
        ContextOperation,
        _preparation_values(owner_id, conversation_id, key, fingerprint, identifier, lease_id),
        index_elements=["idempotency_key"],
    )
    if retry.rowcount == 1:
        return "winner", identifier, lease_id, None
    existing = await claim_db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
    outcome = _existing_claim_outcome(existing, owner_id, fingerprint)
    if outcome is not None:
        return outcome
    raise HTTPException(503, detail={"code": "ACTION_REQUEST_FAILED"})


async def _claim_preparation(factory, *, owner_id, conversation_id, key, fingerprint):
    async with factory() as claim_db, claim_db.begin():
        conversation = await claim_db.get(Conversation, conversation_id)
        if not conversation or conversation.owner_id != owner_id:
            raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND"})
        identifier, lease_id = str(uuid4()), str(uuid4())
        result = await insert_ignore(
            claim_db,
            ContextOperation,
            _preparation_values(owner_id, conversation_id, key, fingerprint, identifier, lease_id),
            index_elements=["idempotency_key"],
        )
        if result.rowcount == 1:
            return "winner", identifier, lease_id, None
        existing = await claim_db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
        outcome = _existing_claim_outcome(existing, owner_id, fingerprint)
        if outcome is not None:
            return outcome
        return await _recover_expired_claim(claim_db, existing, owner_id, conversation_id, key, fingerprint)


async def _store_prepared(factory, preparation_id, lease_id, prepared):
    try:
        serialized = json.dumps(prepared, allow_nan=False, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, detail={"code": "ACTION_PREPARATION_INVALID"}) from exc
    if len(serialized.encode()) > PREPARED_MAX_BYTES:
        raise HTTPException(409, detail={"code": "ACTION_PREPARATION_INVALID"})
    async with factory() as db, db.begin():
        row = await db.get(ContextOperation, preparation_id)
        if row is None or row.outcome != "preparing" or row.lease_id != lease_id:
            raise HTTPException(409, detail={"code": "ACTION_LEASE_LOST"})
        payload = {**(row.payload or {}), "prepared": json.loads(serialized)}
        changed = await db.execute(
            update(ContextOperation)
            .where(
                ContextOperation.id == preparation_id,
                ContextOperation.outcome == "preparing",
                ContextOperation.lease_id == lease_id,
            )
            .values(payload=payload)
        )
        if changed.rowcount != 1:
            raise HTTPException(409, detail={"code": "ACTION_LEASE_LOST"})


async def _wait_for_preparation(factory, *, key, owner_id, fingerprint):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        async with factory() as db:
            existing = await db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
            if existing is None:
                raise HTTPException(503, detail={"code": "ACTION_REQUEST_UNKNOWN"})
            if existing.owner_id != owner_id or existing.payload.get("fingerprint") != fingerprint:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            if existing.outcome == "completed":
                return _replay_result(existing)
            if existing.outcome in {"failed", "unknown"}:
                raise HTTPException(503, detail={"code": "ACTION_REQUEST_FAILED"})
    raise HTTPException(409, detail={"code": "ACTION_IN_PROGRESS"})


async def _delete_preparation(factory, preparation_id, lease_id):
    async with factory() as db, db.begin():
        await db.execute(
            delete(ContextOperation).where(
                ContextOperation.id == preparation_id,
                ContextOperation.outcome == "preparing",
                ContextOperation.lease_id == lease_id,
            )
        )


async def _dispatch_transaction(
    db,
    *,
    owner_id,
    conversation_id,
    request_id,
    expected_revision,
    action,
    catalog,
    handlers,
    prepared=None,
    prepared_operation_id=None,
    preparation_lease=None,
):
    # Existing gate serializes read-modify-write transactions on SQLite and PostgreSQL.
    await db.execute(update(SessionGate).where(SessionGate.id == 1).values(marker=1))
    conversation = await db.get(Conversation, conversation_id)
    if not conversation or conversation.owner_id != owner_id:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND"})
    key = _dispatch_key(owner_id, request_id)
    fingerprint = _dispatch_fingerprint(conversation_id, expected_revision, action)
    existing = await db.scalar(select(ContextOperation).where(ContextOperation.idempotency_key == key))
    replayed = _replay(existing, owner_id, fingerprint)
    if replayed is not None:
        return replayed
    if prepared_operation_id is not None:
        claim = await db.get(ContextOperation, prepared_operation_id)
        if claim is None or claim.outcome != "preparing" or claim.lease_id != preparation_lease:
            raise HTTPException(409, detail={"code": "ACTION_LEASE_LOST"})
        if prepared is None:
            prepared = (claim.payload or {}).get("prepared")
            if prepared is None:
                raise HTTPException(409, detail={"code": "ACTION_LEASE_LOST"})
    if isinstance(action, SETTING_ACTIONS):
        result = await _apply_setting(db, owner_id, conversation_id, expected_revision, action, request_id)
    elif isinstance(action, (ShowConversationHistory, OpenMemoryExplorer)):
        result = _navigation_result(action)
    else:
        result = await _run_handler(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            expected_revision=expected_revision,
            action=action,
            handlers=handlers,
            prepared=prepared,
        )
    await _record_completion(
        db,
        prepared_operation_id=prepared_operation_id,
        owner_id=owner_id,
        conversation_id=conversation_id,
        key=key,
        fingerprint=fingerprint,
        result=result,
        preparation_lease=preparation_lease,
    )
    return result


def _dispatch_key(owner_id, request_id) -> str:
    return hashlib.sha256(("dispatch:" + owner_id + ":" + request_id).encode()).hexdigest()


def _dispatch_fingerprint(conversation_id, expected_revision, action) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "conversation_id": conversation_id,
                "expected_revision": expected_revision,
                "action": action.model_dump(mode="json"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _replay(existing, owner_id, fingerprint):
    if not existing:
        return None
    if existing.owner_id != owner_id or existing.payload.get("fingerprint") != fingerprint:
        raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
    if existing.outcome == "completed":
        return existing.payload["result"]
    return None


async def _apply_setting(db, owner_id, conversation_id, expected_revision, action, request_id):
    row = await db.get(UserSetting, owner_id)
    before = settings_value(row)
    if before.revision != expected_revision:
        raise HTTPException(409, detail={"code": "SETTINGS_REVISION_CONFLICT"})
    after = apply_settings_action(before, action)
    if after.revision != before.revision:
        if row is None:
            row = UserSetting(owner_id=owner_id)
            db.add(row)
        for field in ("speaking_speed", "captions_visible", "approved_voice_id", "revision"):
            setattr(row, field, getattr(after, field))
        row.response_preference = after.response_preference.value
        row.updated_at = now()
        db.add(
            SettingRevision(
                id=str(uuid4()), owner_id=owner_id, revision=after.revision, settings=after.model_dump(mode="json")
            )
        )
    result = {"kind": action.kind, "settings": after.model_dump(mode="json"), "changed": after != before}
    if after != before and isinstance(action, SelectApprovedVoice):
        control = await _authorize_voice_control(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            voice_id=action.voice_id,
            settings_revision=after.revision,
            request_id=request_id,
        )
        if control is not None:
            result["voice_control"] = control
    return result


async def _authorize_voice_control(db, *, owner_id, conversation_id, voice_id, settings_revision, request_id):
    """Record a server-authorized change when this conversation has a live room.

    A settings row alone never mutates TTS. The worker only receives a signed
    control after the catalog, owner and exact session/room are validated here.
    """
    voice = await db.scalar(
        select(VoiceSession)
        .where(
            VoiceSession.owner_id == owner_id,
            VoiceSession.conversation_id == conversation_id,
            VoiceSession.status.in_(["starting", "active"]),
            VoiceSession.expires_at > now(),
        )
        .order_by(VoiceSession.expires_at.desc())
        .limit(1)
    )
    if voice is None:
        return None
    await db.execute(
        update(VoiceControl)
        .where(
            VoiceControl.session_id == voice.id,
            VoiceControl.status.in_(["pending", "dispatched"]),
        )
        .values(status="superseded", updated_at=now())
    )
    control = VoiceControl(
        id=str(uuid4()),
        request_id=request_id,
        session_id=voice.id,
        owner_id=owner_id,
        conversation_id=conversation_id,
        voice_id=voice_id,
        settings_revision=settings_revision,
        status="pending",
        expires_at=control_expiry(),
        created_at=now(),
        updated_at=now(),
    )
    db.add(control)
    await db.flush()
    return voice_state(voice, control)


def _navigation_result(action) -> dict:
    return {
        "kind": action.kind,
        "navigation": "history" if isinstance(action, ShowConversationHistory) else "memory_explorer",
    }


async def _run_handler(db, *, owner_id, conversation_id, request_id, expected_revision, action, handlers, prepared):
    handler = handlers.get(action.kind)
    if handler is None:
        raise HTTPException(503, detail={"code": "ACTION_HANDLER_UNAVAILABLE"})
    # Durable context/memory handlers must retain this transaction; the dispatcher commits once.
    prepare_args = {"prepared": prepared} if getattr(handler, "prepare", None) is not None else {}
    return await handler(
        db=EffectSession(db),
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        expected_revision=expected_revision,
        action=action,
        **prepare_args,
    )


async def _record_completion(
    db, *, prepared_operation_id, owner_id, conversation_id, key, fingerprint, result, preparation_lease=None
) -> None:
    if prepared_operation_id is not None:
        changed = await db.execute(
            update(ContextOperation)
            .where(
                ContextOperation.id == prepared_operation_id,
                ContextOperation.outcome == "preparing",
                ContextOperation.lease_id == preparation_lease,
            )
            .values(
                outcome="completed",
                payload={"fingerprint": fingerprint, "result": result},
                lease_id=None,
                lease_expires_at=None,
            )
        )
        if changed.rowcount != 1:
            # A takeover fenced this worker; roll back its effects.
            raise HTTPException(409, detail={"code": "ACTION_LEASE_LOST"})
        return
    db.add(
        ContextOperation(
            id=str(uuid4()),
            owner_id=owner_id,
            conversation_id=conversation_id,
            command_type="DispatchAction",
            idempotency_key=key,
            outcome="completed",
            payload={"fingerprint": fingerprint, "result": result},
        )
    )
