import hashlib
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.domain.context import ContextCandidate, compile_turn_context, validate_assembly
from app.domain.contracts import (
    TIER_CAPS,
    MemoryRecord,
    MemorySource,
    ModelRequest,
    ResponsePreference,
    UserSettings,
    resolve_response_policy,
)
from app.domain.pipeline import KEYS, PRESET, PipelineConfig, validate_pipeline
from app.domain.policy import normalize_pipeline, pipeline_instructions
from app.domain.sessions import admission_error
from app.domain.weather_permissions import decide_weather_request
from app.infrastructure.database import (
    Base,
    Conversation,
    Identity,
    Memory,
    SessionGate,
    ToolCall,
    Turn,
    UserSetting,
    VoiceSession,
)
from app.services.budget import reserve as reserve_development_budget
from app.services.context_assembly import location_evidence, read_context_inputs
from app.services.context_operations import (
    ContextOperationError,
    confirm_proposal,
    context_state,
    create_proposal,
    list_operations,
    undo_operation,
)
from app.services.session_cleanup import cleanup_room
from app.services.voices import ElevenLabsVoices, VoiceCatalog, VoiceCatalogError
from app.services.weather import WeatherError, get_current_weather

COOKIE = 'sarjy_demo'

class StartVoice(BaseModel):
    conversation_id: str
    language: Literal["ar", "en"] = "en"
    voice_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    response_tier: int | None = Field(default=None, ge=1, le=len(TIER_CAPS))
    response_preference: ResponsePreference = ResponsePreference.AUTOMATIC

class BudgetReservation(BaseModel):
    provider: Literal['openai','elevenlabs']
    characters: int = Field(default=0, ge=0, le=500)

class AgentInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=12000)

class AgentOutput(BaseModel):
    turn_id: str
    text: str = Field(max_length=20000)
    status: Literal['completed','interrupted','failed']

class WeatherInput(BaseModel):
    turn_id: str
    request_id: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=120)
    country: str | None = Field(default=None, max_length=120)

class ContextProposalInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=200)
    keep: tuple[str, ...] = ()
    limit: int = Field(default=20, ge=1, le=20)

class ContextConfirmInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    proposal: str = Field(min_length=1, max_length=20000)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=100)

class ContextUndoInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=100)

class ContextDiff(BaseModel):
    added: list[str]
    removed: list[str]
    kept: list[str]
    revision_before: int
    revision_after: int

class ContextMatch(BaseModel):
    id: str
    content: str
    source_turn_id: str | None
    score: float
    kind: str

class ContextPreserved(BaseModel):
    id: str
    content: str
    reason: str

class ContextProposal(BaseModel):
    conversation_id: str
    query: str
    revision: int
    proposal: str
    matches: list[ContextMatch]
    preserved: list[ContextPreserved]
    diff: ContextDiff

class ContextConfirmation(BaseModel):
    operation_id: str
    status: str
    idempotent: bool
    excluded_ids: list[str]
    kept_ids: list[str]
    revision_before: int
    revision_after: int
    diff: ContextDiff

class ContextUndoResult(BaseModel):
    operation_id: str
    restored_operation_id: str
    status: str
    idempotent: bool
    restored_ids: list[str]
    revision_before: int
    revision_after: int
    diff: ContextDiff

class ContextOperationItem(BaseModel):
    id: str
    command_type: str
    outcome: str
    revision_before: int | None = None
    revision_after: int | None = None
    idempotency_key: str
    created_at: str
    diff: ContextDiff | None

class ContextOperationList(BaseModel):
    conversation_id: str
    revision: int
    operations: list[ContextOperationItem]

class ContextState(BaseModel):
    conversation_id: str
    revision: int
    excluded_ids: list[str]


def _context_error(exc: ContextOperationError) -> HTTPException:
    return HTTPException(exc.status, detail={'code': exc.code})


def create_app(settings: Settings | None = None, *, voice_catalog: ElevenLabsVoices | None = None, embedding_adapter=None):
    config = settings or Settings()
    catalog = voice_catalog or ElevenLabsVoices(config.eleven_api_key)
    engine = create_async_engine(config.database_url)
    if config.database_url.startswith('sqlite'):
        @event.listens_for(engine.sync_engine, 'connect')
        def foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app):
        # Local bootstrap only. Production uses Alembic before application startup.
        if config.database_url.startswith('sqlite'):
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions() as db:
                if not await db.get(SessionGate, 1):
                    db.add(SessionGate(id=1, marker=1))
                    await db.commit()
        yield
        await engine.dispose()

    if config.development_protected and not config.web_service_secret:
        raise ValueError('WEB_SERVICE_SECRET_REQUIRED')
    app = FastAPI(title='Sarjy API', version='0.1.0', lifespan=lifespan, docs_url=None if config.development_protected else '/docs', redoc_url=None if config.development_protected else '/redoc', openapi_url=None if config.development_protected else '/openapi.json')

    @app.middleware('http')
    async def protect_web_api(request, call_next):
        if config.development_protected and request.url.path.startswith('/v1/'):
            supplied = request.headers.get('x-web-service-secret', '')
            if not hmac.compare_digest(supplied, config.web_service_secret):
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': {'code': 'WEB_AUTH_REQUIRED'}}, status_code=401)
        return await call_next(request)
    app.state.settings = config

    async def database():
        async with sessions() as db:
            yield db

    async def identity(request: Request, db: AsyncSession = Depends(database)):
        token = request.cookies.get(COOKIE, '')
        user = await db.scalar(select(Identity).where(Identity.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        if not user or user.expires_at <= datetime.now(UTC).isoformat():
            raise HTTPException(401, detail={'code': 'DEMO_SESSION_REQUIRED'})
        return user

    async def owned(conversation_id: str, user: Identity, db: AsyncSession):
        conversation = await db.get(Conversation, conversation_id)
        if not conversation or conversation.owner_id != user.id:
            raise HTTPException(404, detail={'code': 'CONVERSATION_NOT_FOUND'})
        return conversation

    from app.routes.actions import register_action_routes
    register_action_routes(app, database, identity, owned, catalog)

    from app.domain.context_operations import MemoryCandidate, classify
    from app.routes.memory import memory_router
    from app.services.context_actions import build_context_action_handlers
    from app.services.memory import MemoryService, OpenAIEmbedder
    embedder = embedding_adapter or OpenAIEmbedder(config.openai_api_key)

    async def semantic_search(db, *, owner_id, conversation_id, query, limit):
        try:
            rows = await MemoryService(db, embedder).search_memories(owner_id, conversation_id, query, limit)
        except ValueError as exc:
            raise HTTPException(429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422,
                detail={"code": str(exc)}) from exc
        return tuple(MemoryCandidate(id=row.id, content=row.content, source_turn_id=row.source_turn_id,
            score=1.0 - (row.distance or 0.0), kind=classify(row.content)) for row in rows)

    app.state.action_handlers.update(build_context_action_handlers(
        secret=config.context_proposal_secret or config.agent_service_secret, search=semantic_search))
    app.include_router(memory_router(database, identity, embedder))

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.post('/v1/demo-session')
    async def demo(request: Request, response: Response, db: AsyncSession = Depends(database)):
        token = request.cookies.get(COOKIE, '')
        existing = await db.scalar(select(Identity).where(Identity.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        if existing and existing.expires_at > datetime.now(UTC).isoformat():
            return {'id': existing.id}
        token = secrets.token_urlsafe(32)
        user = Identity(id=str(uuid4()), token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=(datetime.now(UTC) + timedelta(hours=config.demo_ttl_hours)).isoformat())
        db.add(user)
        await db.commit()
        response.set_cookie(COOKIE, token, httponly=True, secure=config.cookie_secure, samesite='lax', max_age=config.demo_ttl_hours*3600, path='/')
        return {'id': user.id}

    @app.get('/v1/conversations')
    async def conversations(user=Depends(identity), db: AsyncSession = Depends(database)):
        rows = (await db.scalars(select(Conversation).where(Conversation.owner_id == user.id).order_by(Conversation.created_at.desc()))).all()
        return [{'id': c.id, 'title': c.title, 'revision': c.revision} for c in rows]

    @app.post('/v1/conversations', status_code=201)
    async def new_conversation(user=Depends(identity), db: AsyncSession = Depends(database)):
        conversation = Conversation(id=str(uuid4()), owner_id=user.id)
        db.add(conversation)
        await db.commit()
        return {'id': conversation.id, 'title': conversation.title, 'revision': conversation.revision}

    @app.get('/v1/conversations/{conversation_id}/turns')
    async def turns(conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        await owned(conversation_id, user, db)
        rows = (await db.scalars(select(Turn).where(Turn.conversation_id == conversation_id).order_by(Turn.created_at, Turn.id))).all()
        return [{'id': t.id, 'role': t.role, 'text': t.text, 'status': t.status} for t in rows]

    def configured_keys():
        return {key for key in {*KEYS.values(), 'DEEPSEEK_API_KEY'} if getattr(config, key.lower())}

    def default_llm():
        return ('openai_direct','gpt-6-luna') if config.default_llm_provider == 'openai_direct' else ('deepseek_direct','deepseek-flash')

    @app.get('/v1/pipelines/providers')
    def providers():
        choices = PRESET | {'llm':default_llm()}
        return {role: {'provider': provider, 'model': model, 'configured': bool(getattr(config, 'deepseek_api_key' if provider == 'deepseek_direct' else KEYS[role].lower()))} for role, (provider, model) in choices.items()}

    @app.get('/v1/voices', response_model=VoiceCatalog)
    async def voices(search: str = Query(default='', max_length=120), next_page_token: str | None = Query(default=None, max_length=2000), user=Depends(identity)):
        try:
            return await catalog.list(search, next_page_token)
        except VoiceCatalogError as exc:
            raise HTTPException(exc.status, detail={'code':exc.code}) from exc

    @app.post('/v1/pipelines/validate')
    def validate(pipeline: PipelineConfig):
        errors = validate_pipeline(pipeline, configured_keys())
        return {'valid': not errors, 'errors': errors}

    @app.get('/v1/weather')
    async def weather(city: str, country: str | None = None, user=Depends(identity)):
        if not city.strip() or len(city) > 120:
            raise HTTPException(422, detail={'code': 'CITY_REQUIRED'})
        try:
            return await get_current_weather(city, country)
        except WeatherError as exc:
            raise HTTPException(422 if str(exc) != 'WEATHER_UNAVAILABLE' else 503, detail={'code': str(exc)}) from exc

    async def worker_session(request: Request, db: AsyncSession, allow_ended: bool = False):
        secret = request.headers.get('x-agent-secret', '')
        if not config.agent_service_secret or not hmac.compare_digest(secret, config.agent_service_secret):
            raise HTTPException(401, detail={'code': 'WORKER_AUTH_REQUIRED'})
        voice = await db.get(VoiceSession, request.headers.get('x-session-id', ''))
        statuses = {'starting', 'active', 'ended'} if allow_ended else {'starting', 'active'}
        cutoff = datetime.now(UTC) - timedelta(seconds=60 if allow_ended else 0)
        if not voice or voice.room_name != request.headers.get('x-room-name') or voice.status not in statuses or voice.expires_at <= cutoff.isoformat():
            raise HTTPException(403, detail={'code': 'SESSION_NOT_ACTIVE'})
        return voice

    @app.post('/v1/voice/sessions', status_code=201)
    async def start_voice(body: StartVoice, user=Depends(identity), db: AsyncSession = Depends(database)):
        await owned(body.conversation_id, user, db)
        required = ['livekit_url', 'livekit_api_key', 'livekit_api_secret', 'agent_service_secret']
        missing = [name.upper() for name in required if not getattr(config, name)]
        policy = resolve_response_policy(body.response_preference, body.response_tier)
        pipeline = PipelineConfig(
            stt={'provider':'deepgram_direct','model':'nova-3','language':body.language},
            llm={'provider':default_llm()[0],'model':default_llm()[1],'language':body.language},
            tts={'provider':'elevenlabs_direct','model':'eleven_flash_v2_5','voice_id':body.voice_id,'language':body.language},
            response_tier=policy.tier,
            response_preference=policy.preference,
        )
        errors = validate_pipeline(pipeline, configured_keys())
        configuration_errors = [error for error in errors if error["code"] != "VOICE_REQUIRED"]
        if missing or configuration_errors:
            raise HTTPException(503, detail={'code':'PIPELINE_NOT_CONFIGURED','missing':missing,'errors':configuration_errors})
        if not body.voice_id:
            raise HTTPException(422, detail={'code':'VOICE_REQUIRED'})
        try:
            await catalog.require(body.voice_id)
        except VoiceCatalogError as exc:
            raise HTTPException(exc.status, detail={'code':exc.code}) from exc
        # Serialize reservations across API processes on both SQLite and PostgreSQL.
        await db.execute(update(SessionGate).where(SessionGate.id == 1).values(marker=1))
        now = datetime.now(UTC)
        await db.execute(update(VoiceSession).where(VoiceSession.expires_at <= now.isoformat(), VoiceSession.status.in_(['starting','active'])).values(status='ended'))
        active = (await db.scalars(select(VoiceSession).where(VoiceSession.status.in_(['starting','active'])))).all()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        month_calls = await db.scalar(select(func.count()).select_from(VoiceSession).where(VoiceSession.expires_at >= month_start))
        identity_calls = await db.scalar(select(func.count()).select_from(VoiceSession).where(VoiceSession.owner_id == user.id))
        rejection = admission_error(tuple(v.owner_id for v in active), user.id, month_calls, identity_calls, config.demo_session_budget, config.demo_calls_per_identity)
        if rejection:
            raise HTTPException(429, detail={'code':rejection})
        voice = VoiceSession(id=str(uuid4()), owner_id=user.id, conversation_id=body.conversation_id, room_name='sarjy-'+str(uuid4()), status='starting', expires_at=(now+timedelta(minutes=10)).isoformat(), pipeline=pipeline.model_dump())
        db.add(voice)
        await db.commit()
        from livekit import api
        try:
            async with api.LiveKitAPI(config.livekit_url, config.livekit_api_key, config.livekit_api_secret) as lk:
                await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(agent_name='sarjy', room=voice.room_name, metadata=json.dumps({'session_id':voice.id})))
            token = api.AccessToken(config.livekit_api_key, config.livekit_api_secret).with_identity(user.id).with_ttl(timedelta(minutes=10)).with_grants(api.VideoGrants(room_join=True, room=voice.room_name, can_publish=True, can_subscribe=True, can_publish_data=True)).to_jwt()
            return {'id':voice.id, 'url':config.livekit_url, 'token':token, 'expires_at':voice.expires_at}
        except Exception:
            await db.execute(update(VoiceSession).where(VoiceSession.id == voice.id).values(status='failed'))
            await db.commit()
            await db.refresh(voice)
            try:
                await cleanup_room(db, voice, config)
            except ValueError:
                await db.rollback()
            raise HTTPException(503, detail={'code':'VOICE_DISPATCH_FAILED'}) from None

    @app.post('/v1/voice/sessions/{session_id}/end')
    async def end_voice(session_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        voice = await db.get(VoiceSession, session_id)
        if not voice or voice.owner_id != user.id:
            raise HTTPException(404)
        if voice.status in {'starting','active'}:
            await db.execute(update(VoiceSession).where(VoiceSession.id == voice.id, VoiceSession.status.in_(['starting','active'])).values(status='ended', expires_at=datetime.now(UTC).isoformat()))
            await db.commit()
            await db.refresh(voice)
        try:
            outcome = await cleanup_room(db, voice, config)
        except ValueError as exc:
            raise HTTPException(503, detail={'code':str(exc), 'cleanup':'pending'}) from exc
        return {'status':voice.status, 'cleanup':outcome}

    @app.post('/internal/development/reserve')
    async def development_reservation(body: BudgetReservation, request: Request, db: AsyncSession = Depends(database)):
        if not config.agent_service_secret or not hmac.compare_digest(request.headers.get('x-agent-secret',''),config.agent_service_secret):
            raise HTTPException(401)
        try:
            return await reserve_development_budget(db, body.provider, body.characters)
        except ValueError as exc:
            raise HTTPException(429,detail={'code':str(exc)}) from exc

    @app.post('/internal/agent/bootstrap')
    async def bootstrap(request: Request, db: AsyncSession = Depends(database)):
        voice = await worker_session(request, db)
        changed = await db.execute(update(VoiceSession).where(VoiceSession.id == voice.id, VoiceSession.status.in_(['starting','active']), VoiceSession.expires_at > datetime.now(UTC).isoformat()).values(status='active'))
        await db.commit()
        if not changed.rowcount:
            raise HTTPException(403, detail={'code':'SESSION_NOT_ACTIVE'})
        pipeline = normalize_pipeline(voice.pipeline)
        return {'pipeline':pipeline, 'instructions':pipeline_instructions(pipeline), 'expires_at':voice.expires_at}

    @app.post('/internal/agent/turn/start')
    async def turn_start(body: AgentInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await worker_session(request, db)
        # Serializes worker retries and preserves the conversation's authoritative order.
        conversation = await db.scalar(select(Conversation).where(Conversation.id == voice.conversation_id).with_for_update())
        existing = await db.scalar(select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.request_id == body.request_id))
        if existing:
            return {'turn_id':existing.id, 'context':existing.snapshot}
        turn = Turn(id=str(uuid4()), conversation_id=voice.conversation_id, request_id=body.request_id, role='user', text=body.text, status='completed')
        pipeline = normalize_pipeline(voice.pipeline)
        inputs = await read_context_inputs(db, owner_id=conversation.owner_id, conversation_id=voice.conversation_id)
        compiled = compile_turn_context(
            revision=conversation.revision,
            instructions=pipeline_instructions(pipeline),
            history=inputs.history,
            memories=inputs.memories.included,
            tool_results=inputs.tool_results,
            current_turn=ContextCandidate(id=turn.id, role='user', text=turn.text, origin='history'),
            excluded_ids=inputs.excluded_ids,
            pipeline=pipeline,
        )
        # The weather decision is frozen with the snapshot: the tool call later
        # authorizes against this revision, never against live or stale state.
        turn.snapshot = {**compiled.to_snapshot(), 'weather_policy': inputs.weather_policy()}
        settings_row = await db.get(UserSetting, conversation.owner_id)
        settings_value = UserSettings() if settings_row is None else UserSettings(
            speaking_speed=settings_row.speaking_speed, captions_visible=settings_row.captions_visible,
            approved_voice_id=settings_row.approved_voice_id, response_preference=settings_row.response_preference,
            revision=settings_row.revision)
        memory_rows = (await db.scalars(select(Memory).where(
            Memory.owner_id == conversation.owner_id, Memory.id.in_(compiled.included_ids),
            Memory.status == 'active'))).all()
        model_request = ModelRequest(
            turn_id=turn.id, attempt_id=str(uuid4()), instructions=pipeline_instructions(pipeline),
            selection=compiled.selection, messages=compiled.messages,
            response_policy=resolve_response_policy(pipeline['response_preference'], pipeline['response_tier']),
            settings=settings_value, settings_revision=settings_value.revision, language=pipeline.get('llm', {}).get('language', pipeline.get('language', 'en')),
            memories=tuple(MemoryRecord(id=row.id, owner_id=row.owner_id, content=row.content,
                source=MemorySource(turn_id=row.source_turn_id,
                    conversation_id=row.source_conversation_id, created_at=row.created_at), version=row.version)
                for row in memory_rows))
        turn.snapshot['model_request'] = model_request.model_dump(mode='json')

        validate_assembly(conversation.revision, compiled.manifest, turn.snapshot)
        conversation_id = voice.conversation_id
        db.add(turn)
        if conversation.title == 'New conversation':
            conversation.title = body.text[:80]
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(select(Turn).where(Turn.conversation_id == conversation_id, Turn.request_id == body.request_id))
            if not existing:
                raise
            return {'turn_id':existing.id, 'context':existing.snapshot}
        return {'turn_id':turn.id, 'context':turn.snapshot}

    @app.post('/internal/agent/turn/finalize')
    async def finalize(body: AgentOutput, request: Request, db: AsyncSession = Depends(database)):
        voice = await worker_session(request, db, allow_ended=True)
        user_turn = await db.get(Turn, body.turn_id)
        if not user_turn or user_turn.conversation_id != voice.conversation_id:
            raise HTTPException(404)
        key = 'reply:'+body.turn_id
        existing = await db.scalar(select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.request_id == key))
        if existing:
            return {'id':existing.id}
        turn = Turn(id=str(uuid4()), conversation_id=voice.conversation_id, request_id=key, role='assistant', text=body.text, status=body.status)
        record_ids = (user_turn.snapshot or {}).get('included_record_ids')
        if isinstance(record_ids, list) and all(isinstance(value, str) for value in record_ids):
            turn.snapshot = {'derivation': {'turn_id': user_turn.id, 'record_ids': record_ids}}
        conversation_id = voice.conversation_id
        db.add(turn)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(select(Turn).where(Turn.conversation_id == conversation_id, Turn.request_id == key))
            if not existing:
                raise
            return {'id':existing.id}
        return {'id':turn.id}

    @app.post('/internal/agent/tool/weather')
    async def agent_weather(body: WeatherInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await worker_session(request, db)
        turn = await db.get(Turn, body.turn_id)
        if not turn or turn.conversation_id != voice.conversation_id:
            raise HTTPException(404)
        policy = (turn.snapshot or {}).get('weather_policy') or {}
        decision = decide_weather_request(
            city=body.city,
            country=body.country,
            user_text=turn.text,
            included=location_evidence(policy.get('included_locations', [])),
            excluded=location_evidence(policy.get('excluded_locations', [])),
        )
        if not decision.allowed:
            return {'error': decision.reason or 'LOCATION_EXCLUDED'}
        existing = await db.scalar(select(ToolCall).where(ToolCall.request_id == body.request_id, ToolCall.turn_id == body.turn_id))
        if existing:
            return existing.result
        try:
            result = await get_current_weather(body.city, body.country)
        except WeatherError as exc:
            result = {'error':str(exc)}
        db.add(ToolCall(id=str(uuid4()), turn_id=body.turn_id, request_id=body.request_id, name='get_current_weather', result=result))
        await db.commit()
        return result

    @app.get('/v1/conversations/{conversation_id}/tool-calls')
    async def tool_calls(conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        await owned(conversation_id, user, db)
        rows = (await db.scalars(select(ToolCall).join(Turn).where(Turn.conversation_id == conversation_id))).all()
        return [{'id':row.id, 'turn_id':row.turn_id, 'name':row.name, 'result':row.result} for row in rows]

    def proposal_secret() -> str:
        return config.context_proposal_secret or config.agent_service_secret

    @app.get('/v1/conversations/{conversation_id}/context', response_model=ContextState)
    async def get_context(conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        try:
            return await context_state(db, owner_id=user.id, conversation_id=conversation_id)
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.get('/v1/conversations/{conversation_id}/context/operations', response_model=ContextOperationList)
    async def get_context_operations(conversation_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        try:
            return await list_operations(db, owner_id=user.id, conversation_id=conversation_id)
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.post('/v1/conversations/{conversation_id}/context/proposals', response_model=ContextProposal)
    async def propose_context(conversation_id: str, body: ContextProposalInput, user=Depends(identity), db: AsyncSession = Depends(database)):
        try:
            return await create_proposal(
                db,
                owner_id=user.id,
                conversation_id=conversation_id,
                query=body.query,
                keep=body.keep,
                limit=body.limit,
                secret=proposal_secret(),
                search=semantic_search,
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.post('/v1/conversations/{conversation_id}/context/confirm', response_model=ContextConfirmation)
    async def confirm_context(conversation_id: str, body: ContextConfirmInput, user=Depends(identity), db: AsyncSession = Depends(database)):
        try:
            return await confirm_proposal(
                db,
                owner_id=user.id,
                conversation_id=conversation_id,
                proposal=body.proposal,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
                secret=proposal_secret(),
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc

    @app.post('/v1/conversations/{conversation_id}/context/undo', response_model=ContextUndoResult)
    async def undo_context(conversation_id: str, body: ContextUndoInput, user=Depends(identity), db: AsyncSession = Depends(database)):
        try:
            return await undo_operation(
                db,
                owner_id=user.id,
                conversation_id=conversation_id,
                operation_id=body.operation_id,
                expected_revision=body.expected_revision,
                idempotency_key=body.idempotency_key,
            )
        except ContextOperationError as exc:
            raise _context_error(exc) from exc


    return app

app = create_app()
