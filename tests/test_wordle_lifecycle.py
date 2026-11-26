"""Wordle refresh startup validation and shutdown cleanup."""

import asyncio
from zoneinfo import ZoneInfoNotFoundError

import pytest
from app.config import Settings
from app.main import _shutdown, _stop_refresh_task
from pydantic import ValidationError


def test_invalid_wordle_timezone_fails_configuration_when_enabled():
    with pytest.raises(ValidationError, match="WORDLE_TIMEZONE_INVALID"):
        Settings(_env_file=None, wordle_enabled=True, wordle_timezone="Not/AZone")


def test_disabled_wordle_boots_with_an_invalid_zone():
    settings = Settings(_env_file=None, wordle_enabled=False, wordle_timezone="Not/AZone")
    assert settings.wordle_timezone == "Not/AZone"


def test_disabled_wordle_boots_with_an_empty_zone():
    settings = Settings(_env_file=None, wordle_enabled=False, wordle_timezone="")
    assert settings.wordle_timezone == ""


def test_shutdown_disposes_even_when_the_refresh_task_already_failed():
    class Engine:
        disposed = False

        async def dispose(self):
            self.disposed = True

    async def fail():
        raise ZoneInfoNotFoundError("bad zone")

    async def run():
        task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        engine = Engine()
        await _shutdown(task, engine)
        assert engine.disposed is True

    asyncio.run(run())


def test_stop_refresh_task_does_not_raise_for_a_failed_task():
    async def fail():
        raise RuntimeError("refresh exploded")

    async def run():
        task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        await _stop_refresh_task(task)

    asyncio.run(run())


def test_shutdown_disposes_when_the_task_ignores_cancellation(monkeypatch):
    monkeypatch.setattr("app.main.REFRESH_TASK_SHUTDOWN_TIMEOUT_SECONDS", 0.2)

    class Engine:
        disposed = False

        async def dispose(self):
            self.disposed = True

    async def stubborn():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(30)

    async def run():
        task = asyncio.create_task(stubborn())
        await asyncio.sleep(0)
        engine = Engine()
        started = asyncio.get_running_loop().time()
        await _shutdown(task, engine)
        assert engine.disposed is True
        assert asyncio.get_running_loop().time() - started < 8

    asyncio.run(run())
