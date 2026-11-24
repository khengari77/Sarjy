"""API composition root.

Routes live in :mod:`app.routes`; this module only wires configuration,
persistence, providers and middleware together.
"""

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.dependencies import ACCOUNT_COOKIE, COOKIE, build_dependencies
from app.domain.context_operations import MemoryCandidate, classify
from app.domain.secrets import secret_matches
from app.infrastructure.database import Base, SessionGate
from app.infrastructure.timing import StatementTiming, current_statement_timing, install_statement_timing
from app.services.account_auth import CredentialProvider, SupabaseCredentials
from app.services.accounts import AccountSessionRequired, authorize_account
from app.services.budget import reserve_jev_request
from app.services.context_actions import build_context_action_handlers
from app.services.jev_ui import JevUiClient
from app.services.memory import MemoryService, OpenAIEmbedder
from app.services.reviewer_access import (
    ReviewerAccessDenied,
    ReviewerSessionRequired,
    authorize,
)
from app.services.voices import DeepgramVoices, ElevenLabsVoices, VoiceProvider, approved_voice_ids
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
    try:
        await asyncio.wait_for(task, timeout=5)
    except (asyncio.CancelledError, TimeoutError):
        pass
    except Exception:  # noqa: BLE001 - a failed refresh task must not block shutdown
        logging.getLogger(__name__).exception("wordle refresh task failed")


async def _shutdown(refresh_task, engine, classifier: JevUiClient | None = None) -> None:
    """Always dispose the engine, whatever the refresh task or the classifier did."""
    try:
        await _stop_refresh_task(refresh_task)
    finally:
        try:
            if classifier is not None:
                await classifier.aclose()
        finally:
            await engine.dispose()


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


class InternalTimingMiddleware:
    """Add request and database timings to internal worker responses."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not _route_path(scope).startswith("/internal/"):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        timing = StatementTiming()
        token = current_statement_timing.set(timing)

        async def send_with_timing(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-server-ms", str(int((time.perf_counter() - started) * 1000)).encode()),
                        (b"x-db-ms", str(int(timing.ms)).encode()),
                        (b"x-db-statements", str(timing.count).encode()),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            current_statement_timing.reset(token)


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


ACCOUNT_PUBLIC_PATHS = {"/v1/auth/sign-up", "/v1/auth/sign-in", "/v1/auth/sign-out", "/v1/auth/oauth/exchange"}


def _account_gate(config: Settings, sessions, now):
    async def gate(request, call_next):
        # Deny by default: a route added without its own dependency is still closed.
        path = _route_path(request.scope)
        if not config.account_access_enabled or not path.startswith("/v1/") or path in ACCOUNT_PUBLIC_PATHS:
            return await call_next(request)
        try:
            async with sessions() as db:
                await authorize_account(db, request.cookies.get(ACCOUNT_COOKIE, ""), now())
        except AccountSessionRequired:
            return JSONResponse({"detail": {"code": "ACCOUNT_SESSION_REQUIRED"}}, status_code=401)
        except Exception:
            return JSONResponse({"detail": {"code": "ACCOUNT_SERVICE_UNAVAILABLE"}}, status_code=503)
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


def _jev_ui_classifier(config: Settings, sessions) -> JevUiClient | None:
    """In-process classifier, or ``None`` when the flag is off or no key is set."""
    if not config.jev_commands_enabled or not config.jev_api_key.strip():
        return None

    async def reserve(_request) -> bool:
        return await reserve_jev_request(sessions, limit=config.jev_daily_requests)

    return JevUiClient(config.jev_api_key, reserve)


def engine_options(url: str) -> dict:
    if url.startswith("sqlite"):
        return {}
    # The DATABASE_URL must be the session-mode pooler, whose client limit the default pool size (5 + 10
    # overflow) already respects; recycle before the pooler or the network drops an idle connection. LIFO
    # reuse keeps hitting the most recently used connection, preserving its asyncpg prepared-statement cache
    # and warm TCP/TLS state; the default FIFO rotation reaches a cold connection on each request and pays an
    # extra pooler round trip for every unprepared statement.
    return {"pool_recycle": 240, "pool_use_lifo": True}


async def _warm_pool(engine) -> None:
    """Open a few connections before the first request; best effort and bounded, never a reason to start late."""

    async def warm():
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    try:
        outcomes = await asyncio.wait_for(asyncio.gather(*(warm() for _ in range(3)), return_exceptions=True), 3)
    except TimeoutError:
        logging.getLogger(__name__).warning("Database pool warm-up timed out")
        return
    if any(isinstance(outcome, Exception) for outcome in outcomes):
        logging.getLogger(__name__).warning("Database pool warm-up failed")


def create_app(
    settings: Settings | None = None,
    *,
    voice_catalog: VoiceProvider | None = None,
    embedding_adapter=None,
    clock=None,
    wordle_source=None,
    ui_classifier=None,
    credentials: CredentialProvider | None = None,
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
    catalog = voice_catalog or (
        DeepgramVoices() if config.tts_provider == "deepgram" else ElevenLabsVoices(config.eleven_api_key)
    )
    source = wordle_source or HttpWordleSource(config.wordle_source_url)
    engine = create_async_engine(config.database_url, **engine_options(config.database_url))
    install_statement_timing(engine.sync_engine)
    if config.database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    classifier = _jev_ui_classifier(config, sessions)

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
            else:
                await _warm_pool(engine)
            yield
        finally:
            await _shutdown(refresh_task, engine, classifier)

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
    # Registered first, so it sits inside the gates: the web-service gate stays outermost.
    app.add_middleware(InternalTimingMiddleware)
    app.middleware("http")(_reviewer_gate(config, sessions, now))
    app.middleware("http")(_account_gate(config, sessions, now))
    app.middleware("http")(_web_service_gate(config))

    app.state.settings = config
    app.state.ui_classifier = ui_classifier or classifier
    database, identity, cleanup_user, owned = build_dependencies(sessions, settings=config, clock=now)

    from app.routes.actions import register_action_routes

    approved = approved_voice_ids(config.approved_voice_ids, config.default_voice_id)
    register_action_routes(
        app,
        database,
        identity,
        owned,
        catalog,
        approved_voice_ids=approved,
    )

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

    from app.routes.context import register_context_routes, register_exclusion_routes
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
    register_worker_routes(
        app,
        config=config,
        database=database,
        sessions=sessions,
        embedder=embedder,
        catalog=catalog,
        approved_voice_ids=approved,
        wordle_source=source,
    )
    register_context_routes(app, config=config, database=database, identity=identity, semantic_search=semantic_search)
    register_exclusion_routes(app, config=config, database=database, identity=identity)
    register_wordle_routes(app, config=config, database=database, identity=identity, source=source)
    if config.reviewer_access_enabled:
        from app.routes.reviewer import register_reviewer_routes

        register_reviewer_routes(app, config=config, database=database, clock=now)
    if config.account_access_enabled:
        from app.routes.accounts import register_account_routes

        register_account_routes(
            app,
            config=config,
            database=database,
            clock=now,
            credentials=credentials or SupabaseCredentials(config.supabase_url, config.supabase_publishable_key),
        )

    return app
