"""Health, identity, conversation and provider-catalog endpoints."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import COOKIE
from app.domain.pipeline import KEYS, PRESET, PipelineConfig, validate_pipeline
from app.infrastructure.database import Conversation, Identity, ToolCall, Turn
from app.services.pipeline_settings import configured_keys, default_llm
from app.services.voices import ElevenLabsVoices, VoiceCatalog, VoiceCatalogError
from app.services.weather import WeatherError, get_current_weather


def register_system_routes(app, *, config: Settings, catalog: ElevenLabsVoices, database, identity, owned):
    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/v1/demo-session")
    async def demo(request: Request, response: Response, db: AsyncSession = Depends(database)):
        token = request.cookies.get(COOKIE, "")
        existing = await db.scalar(
            select(Identity).where(Identity.token_hash == hashlib.sha256(token.encode()).hexdigest())
        )
        if existing and existing.expires_at > datetime.now(UTC).isoformat():
            return {"id": existing.id}
        token = secrets.token_urlsafe(32)
        user = Identity(
            id=str(uuid4()),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=(datetime.now(UTC) + timedelta(hours=config.demo_ttl_hours)).isoformat(),
        )
        db.add(user)
        await db.commit()
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=config.cookie_secure,
            samesite="lax",
            max_age=config.demo_ttl_hours * 3600,
            path="/",
        )
        return {"id": user.id}

    @app.get("/v1/conversations")
    async def conversations(user=Depends(identity), db: AsyncSession = Depends(database)):
        rows = (
            await db.scalars(
                select(Conversation).where(Conversation.owner_id == user.id).order_by(Conversation.created_at.desc())
            )
        ).all()
        return [{"id": c.id, "title": c.title, "revision": c.revision} for c in rows]

    @app.post("/v1/conversations", status_code=201)
    async def new_conversation(user=Depends(identity), db: AsyncSession = Depends(database)):
        conversation = Conversation(id=str(uuid4()), owner_id=user.id)
        db.add(conversation)
        await db.commit()
        return {"id": conversation.id, "title": conversation.title, "revision": conversation.revision}

    @app.get("/v1/conversations/{conversation_id}/turns")
    async def turns(conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        await owned(conversation_id, user, db)
        rows = (
            await db.scalars(
                select(Turn).where(Turn.conversation_id == conversation_id).order_by(Turn.created_at, Turn.id)
            )
        ).all()
        return [{"id": t.id, "role": t.role, "text": t.text, "status": t.status} for t in rows]

    @app.get("/v1/conversations/{conversation_id}/tool-calls")
    async def tool_calls(conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        await owned(conversation_id, user, db)
        rows = (await db.scalars(select(ToolCall).join(Turn).where(Turn.conversation_id == conversation_id))).all()
        return [{"id": row.id, "turn_id": row.turn_id, "name": row.name, "result": row.result} for row in rows]

    @app.get("/v1/pipelines/providers")
    def providers():
        choices = PRESET | {"llm": default_llm(config)}
        return {
            role: {
                "provider": provider,
                "model": model,
                "configured": bool(
                    getattr(config, "deepseek_api_key" if provider == "deepseek_direct" else KEYS[role].lower())
                ),
            }
            for role, (provider, model) in choices.items()
        }

    @app.get("/v1/voices", response_model=VoiceCatalog)
    async def voices(
        search: str = Query(default="", max_length=120),
        next_page_token: str | None = Query(default=None, max_length=2000),
        user=Depends(identity),
    ):
        try:
            return await catalog.list(search, next_page_token)
        except VoiceCatalogError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from exc

    @app.post("/v1/pipelines/validate")
    def validate(pipeline: PipelineConfig):
        errors = validate_pipeline(pipeline, configured_keys(config))
        return {"valid": not errors, "errors": errors}

    @app.get("/v1/weather")
    async def weather(city: str, country: str | None = None, user=Depends(identity)):
        if not city.strip() or len(city) > 120:
            raise HTTPException(422, detail={"code": "CITY_REQUIRED"})
        try:
            return await get_current_weather(city, country)
        except WeatherError as exc:
            raise HTTPException(422 if str(exc) != "WEATHER_UNAVAILABLE" else 503, detail={"code": str(exc)}) from exc
