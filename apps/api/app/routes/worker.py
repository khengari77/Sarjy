"""Worker-only endpoints for bootstrap, turns, weather and budget reservations."""

import hmac
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.context import ContextCandidate, compile_turn_context, validate_assembly
from app.domain.contracts import (
    MemoryRecord,
    MemorySource,
    ModelRequest,
    UserSettings,
    resolve_response_policy,
)
from app.domain.policy import normalize_pipeline, pipeline_instructions
from app.domain.weather_permissions import decide_weather_request
from app.infrastructure.database import Conversation, Memory, ToolCall, Turn, UserSetting, VoiceSession
from app.services.budget import reserve as reserve_development_budget
from app.services.context_assembly import location_evidence, read_context_inputs
from app.services.weather import WeatherError, get_current_weather


class BudgetReservation(BaseModel):
    provider: Literal["openai", "elevenlabs"]
    characters: int = Field(default=0, ge=0, le=500)


class AgentInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=12000)


class AgentOutput(BaseModel):
    turn_id: str
    text: str = Field(max_length=20000)
    status: Literal["completed", "interrupted", "failed"]


class WeatherInput(BaseModel):
    turn_id: str
    request_id: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=120)
    country: str | None = Field(default=None, max_length=120)


def _session_guard(config: Settings):
    async def worker_session(request: Request, db: AsyncSession, allow_ended: bool = False):
        secret = request.headers.get("x-agent-secret", "")
        if not config.agent_service_secret or not hmac.compare_digest(secret, config.agent_service_secret):
            raise HTTPException(401, detail={"code": "WORKER_AUTH_REQUIRED"})
        voice = await db.get(VoiceSession, request.headers.get("x-session-id", ""))
        statuses = {"starting", "active", "ended"} if allow_ended else {"starting", "active"}
        cutoff = datetime.now(UTC) - timedelta(seconds=60 if allow_ended else 0)
        if (
            not voice
            or voice.room_name != request.headers.get("x-room-name")
            or voice.status not in statuses
            or voice.expires_at <= cutoff.isoformat()
        ):
            raise HTTPException(403, detail={"code": "SESSION_NOT_ACTIVE"})
        return voice

    return worker_session


def register_worker_routes(app, *, config: Settings, database):
    guard = _session_guard(config)
    _register_budget_routes(app, config=config, database=database)
    _register_session_routes(app, database=database, guard=guard)
    _register_turn_routes(app, database=database, guard=guard)
    _register_tool_routes(app, database=database, guard=guard)


def _register_budget_routes(app, *, config: Settings, database):
    @app.post("/internal/development/reserve")
    async def development_reservation(body: BudgetReservation, request: Request, db: AsyncSession = Depends(database)):
        if not config.agent_service_secret or not hmac.compare_digest(
            request.headers.get("x-agent-secret", ""), config.agent_service_secret
        ):
            raise HTTPException(401)
        try:
            return await reserve_development_budget(db, body.provider, body.characters)
        except ValueError as exc:
            raise HTTPException(429, detail={"code": str(exc)}) from exc


def _register_session_routes(app, *, database, guard):
    @app.post("/internal/agent/bootstrap")
    async def bootstrap(request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db)
        changed = await db.execute(
            update(VoiceSession)
            .where(
                VoiceSession.id == voice.id,
                VoiceSession.status.in_(["starting", "active"]),
                VoiceSession.expires_at > datetime.now(UTC).isoformat(),
            )
            .values(status="active")
        )
        await db.commit()
        if not changed.rowcount:
            raise HTTPException(403, detail={"code": "SESSION_NOT_ACTIVE"})
        pipeline = normalize_pipeline(voice.pipeline)
        return {"pipeline": pipeline, "instructions": pipeline_instructions(pipeline), "expires_at": voice.expires_at}


def _register_turn_routes(app, *, database, guard):
    @app.post("/internal/agent/turn/start")
    async def turn_start(body: AgentInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db)
        # Serializes worker retries and preserves the conversation's authoritative order.
        conversation = await db.scalar(
            select(Conversation).where(Conversation.id == voice.conversation_id).with_for_update()
        )
        existing = await db.scalar(
            select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.request_id == body.request_id)
        )
        if existing:
            return {"turn_id": existing.id, "context": existing.snapshot}
        turn = Turn(
            id=str(uuid4()),
            conversation_id=voice.conversation_id,
            request_id=body.request_id,
            role="user",
            text=body.text,
            status="completed",
        )
        pipeline = normalize_pipeline(voice.pipeline)
        inputs = await read_context_inputs(db, owner_id=conversation.owner_id, conversation_id=voice.conversation_id)
        compiled = compile_turn_context(
            revision=conversation.revision,
            instructions=pipeline_instructions(pipeline),
            history=inputs.history,
            memories=inputs.memories.included,
            tool_results=inputs.tool_results,
            current_turn=ContextCandidate(id=turn.id, role="user", text=turn.text, origin="history"),
            excluded_ids=inputs.excluded_ids,
            pipeline=pipeline,
        )
        # The weather decision is frozen with the snapshot: the tool call later
        # authorizes against this revision, never against live or stale state.
        turn.snapshot = {**compiled.to_snapshot(), "weather_policy": inputs.weather_policy()}
        settings_row = await db.get(UserSetting, conversation.owner_id)
        settings_value = (
            UserSettings()
            if settings_row is None
            else UserSettings(
                speaking_speed=settings_row.speaking_speed,
                captions_visible=settings_row.captions_visible,
                approved_voice_id=settings_row.approved_voice_id,
                response_preference=settings_row.response_preference,
                revision=settings_row.revision,
            )
        )
        memory_rows = (
            await db.scalars(
                select(Memory).where(
                    Memory.owner_id == conversation.owner_id,
                    Memory.id.in_(compiled.included_ids),
                    Memory.status == "active",
                )
            )
        ).all()
        model_request = ModelRequest(
            turn_id=turn.id,
            attempt_id=str(uuid4()),
            instructions=pipeline_instructions(pipeline),
            selection=compiled.selection,
            messages=compiled.messages,
            response_policy=resolve_response_policy(pipeline["response_preference"], pipeline["response_tier"]),
            settings=settings_value,
            settings_revision=settings_value.revision,
            language=pipeline.get("llm", {}).get("language", pipeline.get("language", "en")),
            memories=tuple(
                MemoryRecord(
                    id=row.id,
                    owner_id=row.owner_id,
                    content=row.content,
                    source=MemorySource(
                        turn_id=row.source_turn_id,
                        conversation_id=row.source_conversation_id,
                        created_at=row.created_at,
                    ),
                    version=row.version,
                )
                for row in memory_rows
            ),
        )
        turn.snapshot["model_request"] = model_request.model_dump(mode="json")

        validate_assembly(conversation.revision, compiled.manifest, turn.snapshot)
        conversation_id = voice.conversation_id
        db.add(turn)
        if conversation.title == "New conversation":
            conversation.title = body.text[:80]
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(Turn).where(Turn.conversation_id == conversation_id, Turn.request_id == body.request_id)
            )
            if not existing:
                raise
            return {"turn_id": existing.id, "context": existing.snapshot}
        return {"turn_id": turn.id, "context": turn.snapshot}

    @app.post("/internal/agent/turn/finalize")
    async def finalize(body: AgentOutput, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db, allow_ended=True)
        user_turn = await db.get(Turn, body.turn_id)
        if not user_turn or user_turn.conversation_id != voice.conversation_id:
            raise HTTPException(404)
        key = "reply:" + body.turn_id
        existing = await db.scalar(
            select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.request_id == key)
        )
        if existing:
            return {"id": existing.id}
        turn = Turn(
            id=str(uuid4()),
            conversation_id=voice.conversation_id,
            request_id=key,
            role="assistant",
            text=body.text,
            status=body.status,
        )
        record_ids = (user_turn.snapshot or {}).get("included_record_ids")
        if isinstance(record_ids, list) and all(isinstance(value, str) for value in record_ids):
            turn.snapshot = {"derivation": {"turn_id": user_turn.id, "record_ids": record_ids}}
        conversation_id = voice.conversation_id
        db.add(turn)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(Turn).where(Turn.conversation_id == conversation_id, Turn.request_id == key)
            )
            if not existing:
                raise
            return {"id": existing.id}
        return {"id": turn.id}


def _register_tool_routes(app, *, database, guard):
    @app.post("/internal/agent/tool/weather")
    async def agent_weather(body: WeatherInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await guard(request, db)
        turn = await db.get(Turn, body.turn_id)
        if not turn or turn.conversation_id != voice.conversation_id:
            raise HTTPException(404)
        policy = (turn.snapshot or {}).get("weather_policy") or {}
        decision = decide_weather_request(
            city=body.city,
            country=body.country,
            user_text=turn.text,
            included=location_evidence(policy.get("included_locations", [])),
            excluded=location_evidence(policy.get("excluded_locations", [])),
        )
        if not decision.allowed:
            return {"error": decision.reason or "LOCATION_EXCLUDED"}
        existing = await db.scalar(
            select(ToolCall).where(ToolCall.request_id == body.request_id, ToolCall.turn_id == body.turn_id)
        )
        if existing:
            return existing.result
        try:
            result = await get_current_weather(body.city, body.country)
        except WeatherError as exc:
            result = {"error": str(exc)}
        db.add(
            ToolCall(
                id=str(uuid4()),
                turn_id=body.turn_id,
                request_id=body.request_id,
                name="get_current_weather",
                result=result,
            )
        )
        await db.commit()
        return result
