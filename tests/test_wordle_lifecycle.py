"""Wordle refresh startup validation and shutdown cleanup."""

import asyncio
from zoneinfo import ZoneInfoNotFoundError

import pytest
from app.config import Settings
from app.main import _shutdown, _stop_refresh_task
from pydantic import ValidationError


def test_invalid_wordle_timezone_fails_configuration():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, wordle_timezone="Not/AZone")


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
