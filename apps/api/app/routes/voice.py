"""Voice-session admission, dispatch and cleanup endpoints."""

import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.contracts import TIER_CAPS, ResponsePreference, resolve_response_policy
from app.domain.pipeline import PipelineConfig, validate_pipeline
from app.domain.sessions import admission_error
from app.infrastructure.database import SessionGate, VoiceAdmission, VoiceSession, now
from app.services.pipeline_settings import configured_keys, default_llm
from app.services.session_cleanup import cleanup_room
from app.services.voice_admission import (
    begin_admission,
    bind_session,
    intent_fingerprint,
    mark_dispatched,
    mark_failed,
    mark_session_outcome,
    session_payload,
)
from app.services.voices import ElevenLabsVoices, VoiceCatalogError


class StartVoice(BaseModel):
    conversation_id: str
    language: Literal["ar", "en"] = "en"
    voice_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    response_tier: int | None = Field(default=None, ge=1, le=len(TIER_CAPS))
    response_preference: ResponsePreference = ResponsePreference.AUTOMATIC
    # Optional retry identity. Legacy callers omit it and keep the historical
    # one-session-per-POST behavior.
    request_key: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")


async def validate_start(body: StartVoice, user, db: AsyncSession, *, config: Settings, catalog, owned):
    await owned(body.conversation_id, user, db)
    required = ["livekit_url", "livekit_api_key", "livekit_api_secret", "agent_service_secret"]
    missing = [name.upper() for name in required if not getattr(config, name)]
    policy = resolve_response_policy(body.response_preference, body.response_tier)
    pipeline = PipelineConfig(
        stt={"provider": "deepgram_direct", "model": "nova-3", "language": body.language},
        llm={"provider": default_llm(config)[0], "model": default_llm(config)[1], "language": body.language},
        tts={
            "provider": "elevenlabs_direct",
            "model": "eleven_flash_v2_5",
            "voice_id": body.voice_id,
            "language": body.language,
        },
        response_tier=policy.tier,
        response_preference=policy.preference,
    )
    errors = validate_pipeline(pipeline, configured_keys(config))
    configuration_errors = [error for error in errors if error["code"] != "VOICE_REQUIRED"]
    if missing or configuration_errors:
        raise HTTPException(
            503, detail={"code": "PIPELINE_NOT_CONFIGURED", "missing": missing, "errors": configuration_errors}
        )
    if not body.voice_id:
        raise HTTPException(422, detail={"code": "VOICE_REQUIRED"})
    try:
        await catalog.require(body.voice_id)
    except VoiceCatalogError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    return policy, pipeline


async def reserve_session(
    body: StartVoice, user, db: AsyncSession, policy, pipeline, *, config: Settings
) -> VoiceSession:
    """Serialize reservations across API processes on both SQLite and PostgreSQL."""
    await db.execute(update(SessionGate).where(SessionGate.id == 1).values(marker=1))
    now_dt = datetime.now(UTC)
    timestamp = now_dt.isoformat()
    expired_ids = list(
        await db.scalars(
            select(VoiceSession.id).where(
                VoiceSession.expires_at <= timestamp, VoiceSession.status.in_(["starting", "active"])
            )
        )
    )
    if expired_ids:
        await db.execute(
            update(VoiceSession)
            .where(
                VoiceSession.id.in_(expired_ids),
                VoiceSession.status.in_(["starting", "active"]),
                VoiceSession.expires_at <= timestamp,
            )
            .values(status="ended")
        )
        await db.execute(
            update(VoiceAdmission)
            .where(VoiceAdmission.session_id.in_(expired_ids), VoiceAdmission.state == "dispatched")
            .values(state="expired", updated_at=now())
        )
    active = (await db.scalars(select(VoiceSession).where(VoiceSession.status.in_(["starting", "active"])))).all()
    month_start = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_calls = await db.scalar(
        select(func.count()).select_from(VoiceSession).where(VoiceSession.expires_at >= month_start)
    )
    identity_calls = await db.scalar(
        select(func.count()).select_from(VoiceSession).where(VoiceSession.owner_id == user.id)
    )
    rejection = admission_error(
        tuple(v.owner_id for v in active),
        user.id,
        month_calls,
        identity_calls,
        config.demo_session_budget,
        config.demo_calls_per_identity,
    )
    if rejection:
        raise HTTPException(429, detail={"code": rejection})
    voice = VoiceSession(
        id=str(uuid4()),
        owner_id=user.id,
        conversation_id=body.conversation_id,
        room_name="sarjy-" + str(uuid4()),
        status="starting",
        expires_at=(now_dt + timedelta(minutes=10)).isoformat(),
        pipeline=pipeline.model_dump(),
    )
    db.add(voice)
    return voice


async def dispatch_voice(voice: VoiceSession, *, config: Settings) -> None:
    from livekit import api

    async with api.LiveKitAPI(config.livekit_url, config.livekit_api_key, config.livekit_api_secret) as lk:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="sarjy", room=voice.room_name, metadata=json.dumps({"session_id": voice.id})
            )
        )


async def start_voice_impl(body: StartVoice, user, db: AsyncSession, *, config, catalog, owned):
    policy, pipeline = await validate_start(body, user, db, config=config, catalog=catalog, owned=owned)
    admission_id: str | None = None
    if body.request_key:
        claim = await begin_admission(
            db,
            owner_id=user.id,
            request_key=body.request_key,
            fingerprint=intent_fingerprint(
                conversation_id=body.conversation_id,
                language=body.language,
                voice_id=body.voice_id,
                tier=policy.tier,
                preference=policy.preference,
            ),
            conversation_id=body.conversation_id,
            config=config,
        )
        if claim.payload is not None:
            return claim.payload
        admission_id = claim.admission_id

    try:
        voice = await reserve_session(body, user, db, policy, pipeline, config=config)
    except HTTPException as exc:
        if admission_id is not None:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            await mark_failed(
                db,
                admission_id,
                code=detail.get("code", "VOICE_ADMISSION_FAILED"),
                status=exc.status_code,
            )
        raise

    if admission_id is not None:
        await bind_session(db, admission_id, voice.id)
    await db.commit()

    try:
        await dispatch_voice(voice, config=config)
    except Exception:
        # Without a definitive provider response, delivery is unknown. Never
        # claim the dispatch failed and never delete a room that may be live.
        if admission_id is not None:
            await mark_failed(db, admission_id, code="VOICE_DISPATCH_UNCERTAIN", status=503, uncertain=True)
        await db.execute(update(VoiceSession).where(VoiceSession.id == voice.id).values(status="failed"))
        await db.commit()
        raise HTTPException(503, detail={"code": "VOICE_DISPATCH_UNCERTAIN"}) from None

    if admission_id is not None:
        await mark_dispatched(db, admission_id)
    return session_payload(voice, config, user.id)


async def end_voice_impl(session_id: str, user, db: AsyncSession, *, config) -> dict:
    voice = await db.get(VoiceSession, session_id)
    if not voice or voice.owner_id != user.id:
        raise HTTPException(404)
    if voice.status in {"starting", "active"}:
        await db.execute(
            update(VoiceSession)
            .where(VoiceSession.id == voice.id, VoiceSession.status.in_(["starting", "active"]))
            .values(status="ended", expires_at=datetime.now(UTC).isoformat())
        )
        await db.commit()
        await db.refresh(voice)
    await mark_session_outcome(db, voice.id, "ended")
    await db.commit()
    try:
        outcome = await cleanup_room(db, voice, config)
    except ValueError as exc:
        raise HTTPException(503, detail={"code": str(exc), "cleanup": "pending"}) from exc
    return {"status": voice.status, "cleanup": outcome}


def register_voice_routes(app, *, config: Settings, catalog: ElevenLabsVoices, database, identity, owned):
    @app.post("/v1/voice/sessions", status_code=201)
    async def start_voice(body: StartVoice, user=Depends(identity), db: AsyncSession = Depends(database)):
        return await start_voice_impl(body, user, db, config=config, catalog=catalog, owned=owned)

    @app.post("/v1/voice/sessions/{session_id}/end")
    async def end_voice(session_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        return await end_voice_impl(session_id, user, db, config=config)
