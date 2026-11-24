"""Deliberate cases for ``scripts/diagnose_resources.py``.

These tests are intentionally outside ``testpaths`` so the normal suite never
creates a leaked resource. The first case leaks a disposable aiosqlite
connection inside a reference cycle; the second drops the last external
reference so only the diagnostic boundary collection can reclaim it.
"""

import asyncio
import sqlite3

import aiosqlite

_PENDING: list[dict] = []


async def _open(connection):
    await connection
    return connection


def test_1_leak_origin(tmp_path):
    # Allocate on this test's line so attribution does not depend on a helper frame.
    connection = aiosqlite.connect(tmp_path / "leak.db")
    asyncio.run(_open(connection))
    holder = {"connection": connection}
    holder["self"] = holder
    connection._diagnostic_cycle = holder
    _PENDING.append(holder)


def test_2_later_collection():
    # The connection survives in an unreachable cycle until the diagnostic
    # collects garbage at this test's boundary.
    _PENDING.clear()


def test_3_closed_control(tmp_path):
    async def make_and_close():
        connection = await aiosqlite.connect(tmp_path / "closed.db")
        await connection.close()
        return connection

    asyncio.run(make_and_close())
    raw = sqlite3.connect(tmp_path / "raw.db")
    raw.close()
