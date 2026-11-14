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
from app.infrastructure.database import SessionGate, UserSetting, VoiceSession
from app.services.pipeline_settings import configured_keys, default_llm
from app.services.session_cleanup import cleanup_room
from app.services.voice_control import fail_session_controls
from app.services.voices import ElevenLabsVoices, VoiceCatalogError


class StartVoice(BaseModel):
    conversation_id: str
    language: Literal["ar", "en"] = "en"
    voice_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    response_tier: int | None = Field(default=None, ge=1, le=len(TIER_CAPS))
    response_preference: ResponsePreference = ResponsePreference.AUTOMATIC


def register_voice_routes(app, *, config: Settings, catalog: ElevenLabsVoices, database, identity, owned):
    @app.post("/v1/voice/sessions", status_code=201)
    async def start_voice(body: StartVoice, user=Depends(identity), db: AsyncSession = Depends(database)):
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
        # Serialize reservations across API processes on both SQLite and PostgreSQL.
        await db.execute(update(SessionGate).where(SessionGate.id == 1).values(marker=1))
        now = datetime.now(UTC)
        await db.execute(
            update(VoiceSession)
            .where(VoiceSession.expires_at <= now.isoformat(), VoiceSession.status.in_(["starting", "active"]))
            .values(status="ended")
        )
        active = (await db.scalars(select(VoiceSession).where(VoiceSession.status.in_(["starting", "active"])))).all()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
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
        settings_row = await db.get(UserSetting, user.id)
        voice = VoiceSession(
            id=str(uuid4()),
            owner_id=user.id,
            conversation_id=body.conversation_id,
            room_name="sarjy-" + str(uuid4()),
            status="starting",
            expires_at=(now + timedelta(minutes=10)).isoformat(),
            pipeline=pipeline.model_dump(),
            active_voice_id=body.voice_id,
            active_settings_revision=settings_row.revision if settings_row is not None else 0,
        )
        db.add(voice)
        await db.commit()
        from livekit import api

        try:
            async with api.LiveKitAPI(config.livekit_url, config.livekit_api_key, config.livekit_api_secret) as lk:
                await lk.agent_dispatch.create_dispatch(
                    api.CreateAgentDispatchRequest(
                        agent_name="sarjy", room=voice.room_name, metadata=json.dumps({"session_id": voice.id})
                    )
                )
            token = (
                api.AccessToken(config.livekit_api_key, config.livekit_api_secret)
                .with_identity(user.id)
                .with_ttl(timedelta(minutes=10))
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=voice.room_name,
                        can_publish=True,
                        can_subscribe=True,
                        can_publish_data=True,
                    )
                )
                .to_jwt()
            )
            return {"id": voice.id, "url": config.livekit_url, "token": token, "expires_at": voice.expires_at}
        except Exception:
            await db.execute(update(VoiceSession).where(VoiceSession.id == voice.id).values(status="failed"))
            await db.commit()
            await db.refresh(voice)
            try:
                await cleanup_room(db, voice, config)
            except ValueError:
                await db.rollback()
            raise HTTPException(503, detail={"code": "VOICE_DISPATCH_FAILED"}) from None

    @app.post("/v1/voice/sessions/{session_id}/end")
    async def end_voice(session_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        voice = await db.get(VoiceSession, session_id)
        if not voice or voice.owner_id != user.id:
            raise HTTPException(404)
        if voice.status in {"starting", "active"}:
            await db.execute(
                update(VoiceSession)
                .where(VoiceSession.id == voice.id, VoiceSession.status.in_(["starting", "active"]))
                .values(status="ended", expires_at=datetime.now(UTC).isoformat())
            )
            # In-flight controls can never be ACKed once the room is ending.
            await fail_session_controls(db, voice.id)
            await db.commit()
            await db.refresh(voice)
        try:
            outcome = await cleanup_room(db, voice, config)
        except ValueError as exc:
            raise HTTPException(503, detail={"code": str(exc), "cleanup": "pending"}) from exc
        return {"status": voice.status, "cleanup": outcome}
