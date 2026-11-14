"""API composition root.

Routes live in :mod:`app.routes`; this module only wires configuration,
persistence, providers and middleware together.
"""

import asyncio
import re
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.dependencies import COOKIE, build_dependencies
from app.domain.context_operations import MemoryCandidate, classify
from app.domain.secrets import secret_matches
from app.infrastructure.database import Base, SessionGate
from app.services.context_actions import build_context_action_handlers
from app.services.memory import MemoryService, OpenAIEmbedder
from app.services.reviewer_access import (
    ReviewerAccessDenied,
    ReviewerSessionRequired,
    authorize,
)
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


REVIEWER_CLEANUP_PATH = re.compile(r"^/v1/voice/sessions/[^/]+/end$")
REVIEWER_PUBLIC_PATHS = {"/v1/reviewer/session"}


def _reviewer_gate_exempt(path: str) -> bool:
    return path in REVIEWER_PUBLIC_PATHS or REVIEWER_CLEANUP_PATH.fullmatch(path) is not None


def _route_path(scope) -> str:
    """Return the router-relative path, stripping any ASGI ``root_path``.

    Middleware sees ``root_path + path`` while routing matches the relative
    path. Without this adjustment a deployment mounted under ``--root-path``
    would skip both gates while the router still served ``/v1/*``.
    """
    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        return path[len(root_path) :] or "/"
    return path


def _reviewer_gate(config: Settings, sessions, now):
    async def gate(request, call_next):
        # Only reviewer mode gates routes; development behavior is unchanged.
        if not config.reviewer_access_enabled:
            return await call_next(request)
        path = _route_path(request.scope)
        if not path.startswith("/v1/") or _reviewer_gate_exempt(path):
            return await call_next(request)
        try:
            async with sessions() as db:
                await authorize(db, request.cookies.get(COOKIE, ""), now())
        except ReviewerSessionRequired:
            return JSONResponse({"detail": {"code": "REVIEWER_SESSION_REQUIRED"}}, status_code=401)
        except ReviewerAccessDenied:
            return JSONResponse({"detail": {"code": "REVIEWER_ACCESS_DENIED"}}, status_code=403)
        except Exception:
            return JSONResponse({"detail": {"code": "REVIEWER_UNAVAILABLE"}}, status_code=503)
        return await call_next(request)

    return gate


def _web_service_gate(config: Settings):
    async def protect(request, call_next):
        if config.development_protected and _route_path(request.scope).startswith("/v1/"):
            supplied = request.headers.get("x-web-service-secret", "")
            if not secret_matches(supplied, config.web_service_secret):
                return JSONResponse({"detail": {"code": "WEB_AUTH_REQUIRED"}}, status_code=401)
        return await call_next(request)

    return protect


def create_app(
    settings: Settings | None = None,
    *,
    voice_catalog: ElevenLabsVoices | None = None,
    embedding_adapter=None,
    clock=None,
    wordle_source=None,
):
    config = settings or Settings()
    now = clock or (lambda: datetime.now(UTC))
    # Reject invalid configuration before allocating the engine so a failed
    # create_app never leaves a connection pool behind.
    if config.development_protected and not config.web_service_secret:
        raise ValueError("WEB_SERVICE_SECRET_REQUIRED")
    if config.reviewer_access_enabled and not (
        config.cookie_secure and config.development_protected and config.web_service_secret
    ):
        raise ValueError("REVIEWER_ACCESS_CONFIGURATION_INVALID")
    catalog = voice_catalog or ElevenLabsVoices(config.eleven_api_key)
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

    # The reviewer gate is added first so the web-service-secret middleware
    # stays outermost and the proxy boundary is checked before admission.
    app.middleware("http")(_reviewer_gate(config, sessions, now))
    app.middleware("http")(_web_service_gate(config))

    app.state.settings = config
    database, identity, cleanup_user, owned = build_dependencies(sessions, settings=config, clock=now)

    from app.routes.actions import register_action_routes

    register_action_routes(app, database, identity, owned, catalog)

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

    app.include_router(memory_router(database, identity, embedder))

    from app.routes.context import register_context_routes
    from app.routes.system import register_system_routes
    from app.routes.voice import register_voice_routes
    from app.routes.wordle import register_wordle_routes
    from app.routes.worker import register_worker_routes

    register_system_routes(
        app, config=config, catalog=catalog, database=database, identity=identity, owned=owned, clock=now
    )
    register_voice_routes(
        app,
        config=config,
        catalog=catalog,
        database=database,
        identity=identity,
        cleanup_identity=cleanup_user,
        owned=owned,
    )
    register_worker_routes(app, config=config, database=database, embedder=embedder)
    register_context_routes(app, config=config, database=database, identity=identity, semantic_search=semantic_search)
    register_wordle_routes(app, config=config, database=database, identity=identity, source=source)
    if config.reviewer_access_enabled:
        from app.routes.reviewer import register_reviewer_routes

        register_reviewer_routes(app, config=config, database=database, clock=now)

    return app
