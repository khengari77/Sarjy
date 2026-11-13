"""Resource-lifecycle regressions for the application composition root and test clients."""

import asyncio
from contextlib import asynccontextmanager, closing

import app.main as main
import pytest
from app.config import Settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine


def track_engines(monkeypatch):
    """Record engines created by create_app and every dispose call."""
    engines = []
    disposed = []
    real_factory = main.create_async_engine
    real_dispose = AsyncEngine.dispose

    def factory(url, **kwargs):
        engine = real_factory(url, **kwargs)
        engines.append(engine)
        return engine

    async def dispose(self, *args, **kwargs):
        disposed.append(self)
        return await real_dispose(self, *args, **kwargs)

    monkeypatch.setattr(main, "create_async_engine", factory)
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    return engines, disposed


def sqlite_settings(path):
    return Settings(database_url=f"sqlite+aiosqlite:///{path}", _env_file=None)


def test_invalid_configuration_is_rejected_before_engine_creation(monkeypatch):
    engines, _ = track_engines(monkeypatch)
    settings = Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        development_protected=True,
        web_service_secret="",
        _env_file=None,
    )
    with pytest.raises(ValueError, match="WEB_SERVICE_SECRET_REQUIRED"):
        create_app(settings)
    # Nothing was allocated, so there is no pool to dispose.
    assert engines == []


def test_startup_failure_disposes_engine(tmp_path, monkeypatch):
    engines, disposed = track_engines(monkeypatch)
    missing_parent = tmp_path / "missing" / "db.sqlite"
    app = create_app(sqlite_settings(missing_parent))
    with pytest.raises(OperationalError), TestClient(app):
        pass
    assert len(engines) == 1
    assert disposed.count(engines[0]) == 1


def test_normal_shutdown_disposes_engine_once(tmp_path, monkeypatch):
    engines, disposed = track_engines(monkeypatch)
    with TestClient(create_app(sqlite_settings(tmp_path / "ok.db"))):
        pass
    assert len(engines) == 1
    assert disposed.count(engines[0]) == 1


def test_shutdown_exception_disposes_engine(tmp_path, monkeypatch):
    engines, disposed = track_engines(monkeypatch)
    app = create_app(sqlite_settings(tmp_path / "boom.db"))

    async def run():
        with pytest.raises(RuntimeError, match="shutdown boom"):
            async with app.router.lifespan_context(app):
                raise RuntimeError("shutdown boom")

    asyncio.run(run())
    assert len(engines) == 1
    assert disposed.count(engines[0]) == 1


def test_secondary_client_shares_running_lifespan_and_is_closed():
    """A second identity must reuse the live lifespan and still be closed explicitly."""
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("start")
        try:
            yield
        finally:
            events.append("stop")

    app = FastAPI(lifespan=lifespan)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as primary:
        with closing(TestClient(app)) as secondary:
            assert not secondary.is_closed
            assert secondary.get("/ping").json() == {"ok": True}
            # Entering no context means no second lifespan was started.
            assert events == ["start"]
        assert secondary.is_closed
        assert primary.get("/ping").status_code == 200

    assert events == ["start", "stop"]
