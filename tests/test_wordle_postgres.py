"""Real PostgreSQL per-game concurrency regressions for Wordle submissions.

These exercise the ``SELECT ... FOR UPDATE`` serialization that is a no-op on
SQLite.  They must run against a disposable, migrated PostgreSQL database.
"""

import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from app.infrastructure.database import Base, Identity, WordleGame, WordleGuess
from app.services.wordle import WordleServiceError, ensure_game, submit_guess
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
REQUIRE_POSTGRES = os.environ.get("REQUIRE_WORDLE_POSTGRES") == "1"

if REQUIRE_POSTGRES and not POSTGRES_URL:
    raise RuntimeError("REQUIRE_WORDLE_POSTGRES=1 but TEST_POSTGRES_URL is empty; refusing to skip")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is not set (disposable PostgreSQL only)",
)

DAY = date(2026, 11, 13)
CRANE = "CRANE"


def _sqlstate(error: BaseException) -> str | None:
    return getattr(getattr(error, "orig", None), "sqlstate", None)


def _assert_not_deadlock(result: object) -> None:
    assert not isinstance(result, BaseException) or isinstance(result, WordleServiceError), (
        f"unexpected database error escaped the service: {result!r}"
    )
    if isinstance(result, BaseException):
        assert _sqlstate(result) != "40P01"


async def _engine():
    engine = create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"deadlock_timeout": "100ms", "statement_timeout": "8000"}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[Identity.__table__, WordleGame.__table__, WordleGuess.__table__],
                checkfirst=True,
            )
        )
    return engine


async def _owner(sessions) -> str:
    key = str(uuid4())
    async with sessions() as db:
        db.add(Identity(id=key, token_hash=key, expires_at="2099-01-01T00:00:00+00:00"))
        await db.commit()
    return key


def test_postgres_existing_game_serializes_and_rejects_the_late_guess():
    async def run():
        engine = await _engine()
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner = await _owner(sessions)
            async with sessions() as db:
                await ensure_game(db, owner_id=owner, day=DAY)

            first_locked = asyncio.Event()
            release = asyncio.Event()
            second_entered = asyncio.Event()

            async def first_hook():
                first_locked.set()
                await release.wait()

            async def second_hook():
                second_entered.set()

            async def guess(word: str, request_id: str, hook):
                async with sessions() as db:
                    return await submit_guess(
                        db,
                        owner_id=owner,
                        day=DAY,
                        answer=CRANE,
                        guess=word,
                        request_id=request_id,
                        _race_hook=hook,
                    )

            first = asyncio.create_task(guess(CRANE, "existing-winner", first_hook))
            await asyncio.wait_for(first_locked.wait(), timeout=5)
            second = asyncio.create_task(guess("SLATE", "existing-late", second_hook))
            await asyncio.sleep(0.3)
            # The late request must be blocked on the game row, not reading it.
            assert not second_entered.is_set()

            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), timeout=10)
            winner, late = results
            _assert_not_deadlock(winner)
            _assert_not_deadlock(late)
            assert not isinstance(winner, BaseException)
            assert winner.game.status == "won"
            assert isinstance(late, WordleServiceError)
            assert late.code == "WORDLE_GAME_OVER"

            async with sessions() as db:
                rows = (
                    await db.scalars(
                        select(WordleGuess).where(WordleGuess.owner_id == owner).order_by(WordleGuess.attempt)
                    )
                ).all()
            assert [(row.attempt, row.word) for row in rows] == [(1, CRANE)]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_first_guess_does_not_deadlock_on_game_creation():
    async def run():
        engine = await _engine()
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            owner = await _owner(sessions)
            first_locked = asyncio.Event()
            release = asyncio.Event()
            second_entered = asyncio.Event()

            async def first_hook():
                first_locked.set()
                await release.wait()

            async def second_hook():
                second_entered.set()

            async def guess(word: str, request_id: str, hook):
                async with sessions() as db:
                    return await submit_guess(
                        db,
                        owner_id=owner,
                        day=DAY,
                        answer=CRANE,
                        guess=word,
                        request_id=request_id,
                        _race_hook=hook,
                    )

            first = asyncio.create_task(guess("SLATE", "fresh-first", first_hook))
            await asyncio.wait_for(first_locked.wait(), timeout=5)
            second = asyncio.create_task(guess(CRANE, "fresh-winner", second_hook))
            await asyncio.sleep(0.3)
            # Both requests must lock the same game row first; the second
            # cannot reach the status/insert section while the first holds it.
            assert not second_entered.is_set()

            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), timeout=10)
            for result in results:
                _assert_not_deadlock(result)

            async with sessions() as db:
                rows = (
                    await db.scalars(
                        select(WordleGuess).where(WordleGuess.owner_id == owner).order_by(WordleGuess.attempt)
                    )
                ).all()
            attempts = [row.attempt for row in rows]
            assert attempts[:1] == [1]
            assert len(attempts) in (1, 2)
            assert len(set(attempts)) == len(attempts)
            assert rows[0].word == "SLATE"
        finally:
            await engine.dispose()

    asyncio.run(run())
