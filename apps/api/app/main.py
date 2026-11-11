import hashlib
import secrets
import hmac
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import Literal

from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update, event, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import Settings
from app.domain.pipeline import PipelineConfig, validate_pipeline, PRESET, KEYS
from app.domain.sessions import admission_error
from app.domain.context import ContextRecord, compile_context
from app.infrastructure.database import Base, Identity, Conversation, Turn, VoiceSession, SessionGate, ToolCall
from app.services.voices import ElevenLabsVoices, VoiceCatalog, VoiceCatalogError
from app.services.budget import reserve as reserve_development_budget
from app.services.weather import get_current_weather, WeatherError

COOKIE = 'sarjy_demo'

class StartVoice(BaseModel):
    conversation_id: str
    language: Literal["ar", "en"] = "en"
    voice_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

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


def create_app(settings: Settings | None = None, *, voice_catalog: ElevenLabsVoices | None = None):
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
        if not user or user.expires_at <= datetime.now(timezone.utc).isoformat():
            raise HTTPException(401, detail={'code': 'DEMO_SESSION_REQUIRED'})
        return user

    async def owned(conversation_id: str, user: Identity, db: AsyncSession):
        conversation = await db.get(Conversation, conversation_id)
        if not conversation or conversation.owner_id != user.id:
            raise HTTPException(404, detail={'code': 'CONVERSATION_NOT_FOUND'})
        return conversation

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.post('/v1/demo-session')
    async def demo(request: Request, response: Response, db: AsyncSession = Depends(database)):
        token = request.cookies.get(COOKIE, '')
        existing = await db.scalar(select(Identity).where(Identity.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        if existing and existing.expires_at > datetime.now(timezone.utc).isoformat():
            return {'id': existing.id}
        token = secrets.token_urlsafe(32)
        user = Identity(id=str(uuid4()), token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=(datetime.now(timezone.utc) + timedelta(hours=config.demo_ttl_hours)).isoformat())
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
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60 if allow_ended else 0)
        if not voice or voice.room_name != request.headers.get('x-room-name') or voice.status not in statuses or voice.expires_at <= cutoff.isoformat():
            raise HTTPException(403, detail={'code': 'SESSION_NOT_ACTIVE'})
        return voice

    @app.post('/v1/voice/sessions', status_code=201)
    async def start_voice(body: StartVoice, user=Depends(identity), db: AsyncSession = Depends(database)):
        await owned(body.conversation_id, user, db)
        required = ['livekit_url', 'livekit_api_key', 'livekit_api_secret', 'agent_service_secret']
        missing = [name.upper() for name in required if not getattr(config, name)]
        pipeline = PipelineConfig(stt={'provider':'deepgram_direct','model':'nova-3'}, llm={'provider':default_llm()[0],'model':default_llm()[1]}, tts={'provider':'elevenlabs_direct','model':'eleven_flash_v2_5','voice_id':body.voice_id})
        for choice in (pipeline.stt, pipeline.llm, pipeline.tts):
            choice.language = body.language
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
        now = datetime.now(timezone.utc)
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
            voice.status = 'failed'
            await db.commit()
            raise HTTPException(503, detail={'code':'VOICE_DISPATCH_FAILED'})

    @app.post('/v1/voice/sessions/{session_id}/end')
    async def end_voice(session_id: str, user=Depends(identity), db: AsyncSession = Depends(database)):
        voice = await db.get(VoiceSession, session_id)
        if not voice or voice.owner_id != user.id:
            raise HTTPException(404)
        voice.status = 'ended'
        voice.expires_at = datetime.now(timezone.utc).isoformat()
        await db.commit()
        if config.livekit_api_key:
            from livekit import api
            try:
                async with api.LiveKitAPI(config.livekit_url, config.livekit_api_key, config.livekit_api_secret) as lk:
                    await lk.room.delete_room(api.DeleteRoomRequest(room=voice.room_name))
            except Exception:
                raise HTTPException(503, detail={'code':'ROOM_CLEANUP_FAILED'})
        return {'status':'ended'}

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
        voice.status = 'active'
        await db.commit()
        return {'pipeline':voice.pipeline, 'expires_at':voice.expires_at}

    @app.post('/internal/agent/turn/start')
    async def turn_start(body: AgentInput, request: Request, db: AsyncSession = Depends(database)):
        voice = await worker_session(request, db)
        # Serializes worker retries and preserves the conversation's authoritative order.
        conversation = await db.scalar(select(Conversation).where(Conversation.id == voice.conversation_id).with_for_update())
        existing = await db.scalar(select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.request_id == body.request_id))
        if existing:
            return {'turn_id':existing.id, 'context':existing.snapshot}
        history = (await db.scalars(select(Turn).where(Turn.conversation_id == voice.conversation_id, Turn.status == 'completed').order_by(Turn.created_at.desc()).limit(20))).all()
        turn = Turn(id=str(uuid4()), conversation_id=voice.conversation_id, request_id=body.request_id, role='user', text=body.text, status='completed')
        records = (ContextRecord('system','system','You are Sarjy, a concise bilingual voice assistant. Reply in the language explicitly requested. Use the weather tool for current weather. Ask for a city when absent; never invent weather.'),) + tuple(ContextRecord(t.id,t.role,t.text) for t in reversed(history)) + (ContextRecord(turn.id,'user',turn.text),)
        turn.snapshot = compile_context(records, conversation.revision)
        turn.snapshot['pipeline_config'] = voice.pipeline
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


    return app

app = create_app()
