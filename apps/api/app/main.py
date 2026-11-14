"""API composition root.

Routes live in :mod:`app.routes`; this module only wires configuration,
persistence, providers and middleware together.
"""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.dependencies import build_dependencies
from app.domain.context_operations import MemoryCandidate, classify
from app.domain.secrets import secret_matches
from app.infrastructure.database import Base, SessionGate
from app.services.context_actions import build_context_action_handlers
from app.services.memory import MemoryService, OpenAIEmbedder
from app.services.voice_control import RoomVoiceControl
from app.services.voices import ElevenLabsVoices
from app.services.wordle import HttpWordleSource, wordle_refresh_loop


def _wordle_refresh_task(config: Settings, sessions, source):
    if not config.wordle_enabled or config.wordle_refresh_hour is None:
        return None
    return asyncio.create_task(
        wordle_refresh_loop(
            sessions,
            source,
            timezone=config.wordle_timezone,
            hour=config.wordle_refresh_hour,
        )
    )


async def _stop_refresh_task(task) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def create_app(
    settings: Settings | None = None,
    *,
    voice_catalog: ElevenLabsVoices | None = None,
    voice_control_delivery=None,
    embedding_adapter=None,
    wordle_source=None,
):
    config = settings or Settings()
    # Reject invalid configuration before allocating the engine so a failed
    # create_app never leaves a connection pool behind.
    if config.development_protected and not config.web_service_secret:
        raise ValueError("WEB_SERVICE_SECRET_REQUIRED")
    catalog = voice_catalog or ElevenLabsVoices(config.eleven_api_key)
    delivery = voice_control_delivery or RoomVoiceControl(
        url=config.livekit_url, api_key=config.livekit_api_key, api_secret=config.livekit_api_secret
    )
    source = wordle_source or HttpWordleSource(config.wordle_source_url)
    engine = create_async_engine(config.database_url)
    if config.database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app):
        # Dispose on every exit path: startup failure, normal shutdown and an
        # exception raised while the application is serving requests.
        refresh_task = _wordle_refresh_task(config, sessions, source)
        try:
            # Local bootstrap only. Production uses Alembic before application startup.
            if config.database_url.startswith("sqlite"):
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                async with sessions() as db:
                    if not await db.get(SessionGate, 1):
                        db.add(SessionGate(id=1, marker=1))
                        await db.commit()
            yield
        finally:
            await _stop_refresh_task(refresh_task)
            await engine.dispose()

    app = FastAPI(
        title="Sarjy API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if config.development_protected else "/docs",
        redoc_url=None if config.development_protected else "/redoc",
        openapi_url=None if config.development_protected else "/openapi.json",
    )

    @app.middleware("http")
    async def protect_web_api(request, call_next):
        if config.development_protected and request.url.path.startswith("/v1/"):
            supplied = request.headers.get("x-web-service-secret", "")
            if not secret_matches(supplied, config.web_service_secret):
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": {"code": "WEB_AUTH_REQUIRED"}}, status_code=401)
        return await call_next(request)

    app.state.settings = config
    database, identity, owned = build_dependencies(sessions)

    from app.routes.actions import register_action_routes

    register_action_routes(app, database, identity, owned, catalog, config=config, delivery=delivery)

    embedder = embedding_adapter or OpenAIEmbedder(config.openai_api_key)

    async def semantic_search(db, *, owner_id, conversation_id, query, limit):
        try:
            rows = await MemoryService(db, embedder).search_memories(owner_id, conversation_id, query, limit)
        except ValueError as exc:
            raise HTTPException(
                429 if str(exc) == "DAILY_BUDGET_EXHAUSTED" else 422, detail={"code": str(exc)}
            ) from exc
        return tuple(
            MemoryCandidate(
                id=row.id,
                content=row.content,
                source_turn_id=row.source_turn_id,
                score=1.0 - (row.distance or 0.0),
                kind=classify(row.content),
            )
            for row in rows
        )

    app.state.action_handlers.update(
        build_context_action_handlers(
            secret=config.context_proposal_secret or config.agent_service_secret, search=semantic_search
        )
    )

    from app.routes.memory import memory_router
    from app.routes.onboarding import onboarding_router

    app.include_router(memory_router(database, identity, embedder))
    app.include_router(onboarding_router(database, identity, config))

    from app.routes.context import register_context_routes
    from app.routes.system import register_system_routes
    from app.routes.voice import register_voice_routes
    from app.routes.wordle import register_wordle_routes
    from app.routes.worker import register_worker_routes

    register_system_routes(app, config=config, catalog=catalog, database=database, identity=identity, owned=owned)
    register_voice_routes(app, config=config, catalog=catalog, database=database, identity=identity, owned=owned)
    register_worker_routes(app, config=config, database=database)
    register_context_routes(app, config=config, database=database, identity=identity, semantic_search=semantic_search)
    register_wordle_routes(app, config=config, database=database, identity=identity, source=source)

    return app
