"""Wordle API acceptance: secrecy, idempotency, isolation and computed stats."""

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.config import Settings
from app.domain.wordle import resolve_puzzle_date
from app.infrastructure.database import Base, WordleGame, WordleGuess, WordlePuzzle
from app.main import create_app
from app.routes.wordle import GuessRateLimiter
from app.services.wordle import WordleServiceError, ensure_game, submit_guess
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_wordle_source import FixtureSource

CRANE = "CRANE"
WRONG_GUESSES = ("SLATE", "PIOUS", "DINGY", "BUXOM", "FJORD", "WHACK")


def settings(tmp_path, *, enabled=True):
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/wordle-api.db",
        _env_file=None,
        wordle_enabled=enabled,
        wordle_timezone="America/New_York",
        agent_service_secret="refresh-secret",
    )


def today():
    return resolve_puzzle_date(datetime.now(UTC), "America/New_York").isoformat()


def test_today_starts_one_game_and_keeps_answer_secret(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        assert client.get("/v1/wordle/today").status_code == 401
        client.post("/v1/demo-session")
        first = client.get("/v1/wordle/today")
        assert first.status_code == 200
        body = first.json()
        assert body["available"] is True
        assert body["date"] == today()
        assert body["game"]["status"] == "in_progress"
        assert body["game"]["guesses"] == []
        assert "answer" not in body["game"]
        assert CRANE not in first.text
        second = client.get("/v1/wordle/today").json()
        assert second["game"]["id"] == body["game"]["id"]
        assert source.calls == [today()]


def test_guess_scoring_duplicate_idempotency_and_duplicate_word(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        client.get("/v1/wordle/today")
        scored = client.post("/v1/wordle/guesses", json={"guess": "EERIE", "request_id": "guess-1"})
        assert scored.status_code == 200, scored.text
        body = scored.json()
        assert body["marks"] == ["gray", "gray", "yellow", "gray", "green"]
        assert body["status"] == "in_progress"
        assert body["attempts_remaining"] == 5
        assert "answer" not in body and CRANE not in scored.text

        replay = client.post("/v1/wordle/guesses", json={"guess": "EERIE", "request_id": "guess-1"})
        assert replay.status_code == 200
        assert replay.json()["guesses"] == body["guesses"]
        assert replay.json()["marks"] == body["marks"]
        assert len(replay.json()["guesses"]) == 1

        duplicate = client.post("/v1/wordle/guesses", json={"guess": "EERIE", "request_id": "guess-2"})
        assert duplicate.status_code == 200
        assert len(duplicate.json()["guesses"]) == 2
        assert duplicate.json()["attempts_remaining"] == 4

        conflict = client.post("/v1/wordle/guesses", json={"guess": "SLATE", "request_id": "guess-1"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "WORDLE_REQUEST_CONFLICT"


def test_invalid_word_is_rejected_without_consuming_an_attempt(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        client.get("/v1/wordle/today")
        for invalid in ("CRAN", "CR4NE", "TOOLONGWORD", ""):
            response = client.post("/v1/wordle/guesses", json={"guess": invalid, "request_id": f"bad-{invalid}"})
            assert response.status_code == 422
        assert client.get("/v1/wordle/today").json()["game"]["guesses"] == []


def test_six_wrong_guesses_reveal_answer_once_and_block_more(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        client.get("/v1/wordle/today")
        for attempt, word in enumerate(WRONG_GUESSES, start=1):
            response = client.post("/v1/wordle/guesses", json={"guess": word, "request_id": f"lost-{attempt}"})
            assert response.status_code == 200, response.text
            if attempt < 6:
                assert "answer" not in response.json()
                assert CRANE not in response.text
            else:
                assert response.json()["status"] == "lost"
                assert response.json()["answer"] == CRANE
                assert response.json()["attempts_remaining"] == 0
        blocked = client.post("/v1/wordle/guesses", json={"guess": "CRANE", "request_id": "lost-7"})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "WORDLE_GAME_OVER"
        final = client.get("/v1/wordle/today").json()["game"]
        assert final["status"] == "lost"
        assert final["answer"] == CRANE


def test_winning_guess_reveals_answer(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        client.get("/v1/wordle/today")
        won = client.post("/v1/wordle/guesses", json={"guess": "crane", "request_id": "win-1"})
        assert won.status_code == 200
        assert won.json()["status"] == "won"
        assert won.json()["answer"] == CRANE
        assert won.json()["marks"] == ["green"] * 5


def test_owner_isolation_and_independent_stats(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        mine = client.get("/v1/wordle/today").json()["game"]
        client.post("/v1/wordle/guesses", json={"guess": "SLATE", "request_id": "owner-one"})

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            theirs = other.get("/v1/wordle/today").json()["game"]
            assert theirs["id"] != mine["id"]
            assert theirs["guesses"] == []
            assert other.get("/v1/wordle/stats").json() == {
                "played": 0,
                "won": 0,
                "current_streak": 0,
                "max_streak": 0,
            }
            assert client.get("/v1/wordle/stats").json()["played"] == 0
            client.post("/v1/wordle/guesses", json={"guess": "CRANE", "request_id": "owner-one-win"})
            assert client.get("/v1/wordle/stats").json()["won"] == 1
            assert other.get("/v1/wordle/stats").json()["played"] == 0


def test_two_owners_may_reuse_the_same_request_id(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        client.get("/v1/wordle/today")
        first = client.post("/v1/wordle/guesses", json={"guess": "SLATE", "request_id": "guess-1"})
        assert first.status_code == 200, first.text

        with closing(TestClient(client.app)) as other:
            other.post("/v1/demo-session")
            other.get("/v1/wordle/today")
            second = other.post("/v1/wordle/guesses", json={"guess": "SLATE", "request_id": "guess-1"})
            assert second.status_code == 200, second.text
            assert second.json()["id"] != first.json()["id"]
            assert len(second.json()["guesses"]) == 1
            assert second.json()["attempts_remaining"] == 5

        # Owner-scoped replay still returns the owner's own original row.
        replay = client.post("/v1/wordle/guesses", json={"guess": "SLATE", "request_id": "guess-1"})
        assert replay.status_code == 200
        assert replay.json()["id"] == first.json()["id"]
        assert len(replay.json()["guesses"]) == 1


def test_stats_are_computed_from_stored_games(tmp_path):
    source = FixtureSource()
    config = settings(tmp_path)
    with TestClient(create_app(config, wordle_source=source)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        assert client.get("/v1/wordle/stats").json() == {
            "played": 0,
            "won": 0,
            "current_streak": 0,
            "max_streak": 0,
        }
        rows = (
            ("2026-11-08", "won"),
            ("2026-11-09", "won"),
            ("2026-11-10", "lost"),
            ("2026-11-11", "won"),
            ("2026-11-12", "won"),
        )
        with closing(sqlite3.connect(tmp_path / "wordle-api.db")) as db, db:
            for day, status in rows:
                db.execute(
                    "INSERT INTO wordle_games (id, owner_id, puzzle_date, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), owner, day, status, f"{day}T00:00:00+00:00", f"{day}T00:00:00+00:00"),
                )
        assert client.get("/v1/wordle/stats").json() == {
            "played": 5,
            "won": 4,
            "current_streak": 2,
            "max_streak": 2,
        }


def test_unavailable_source_reports_code_without_answer(tmp_path):
    source = FixtureSource(always_fail=True)
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        response = client.get("/v1/wordle/today")
        assert response.status_code == 200
        assert response.json()["available"] is False
        assert response.json()["code"] == "WORDLE_UNAVAILABLE"
        assert response.json()["game"] is None
        guess = client.post("/v1/wordle/guesses", json={"guess": "SLATE", "request_id": "no-source"})
        assert guess.status_code == 503
        assert guess.json()["detail"]["code"] == "WORDLE_UNAVAILABLE"
        assert CRANE not in response.text + guess.text


def test_kill_switch_prevents_any_source_call(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path, enabled=False), wordle_source=source)) as client:
        client.post("/v1/demo-session")
        body = client.get("/v1/wordle/today").json()
        assert body["available"] is False and body["code"] == "WORDLE_DISABLED"
        assert client.post("/v1/wordle/guesses", json={"guess": "SLATE", "request_id": "off"}).status_code == 503
        assert client.get("/v1/wordle/stats").status_code == 503
        assert client.post("/internal/wordle/refresh", headers={"x-agent-secret": "refresh-secret"}).status_code == 503
    assert source.calls == []


def test_refresh_requires_secret_and_fetches_once(tmp_path):
    source = FixtureSource()
    with TestClient(create_app(settings(tmp_path), wordle_source=source)) as client:
        assert client.post("/internal/wordle/refresh").status_code == 401
        first = client.post("/internal/wordle/refresh", headers={"x-agent-secret": "refresh-secret"})
        assert first.status_code == 200, first.text
        assert first.json() == {"date": today(), "available": True, "source": "fixture://wordle"}
        assert CRANE not in first.text
        second = client.post("/internal/wordle/refresh", headers={"x-agent-secret": "refresh-secret"})
        assert second.status_code == 200
        assert source.calls == [today()]


def test_rate_limiter_counts_per_owner_and_expires():
    limiter = GuessRateLimiter(limit=2, window=10.0)
    assert limiter.allow("alice", now=100.0)
    assert limiter.allow("alice", now=101.0)
    assert not limiter.allow("alice", now=102.0)
    assert limiter.allow("bob", now=102.0)
    assert limiter.allow("alice", now=111.1)


def _concurrent_engine(tmp_path):
    return create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/wordle-concurrent.db",
        connect_args={"timeout": 30},
    )


def test_late_guess_cannot_commit_on_a_game_finished_mid_flight(tmp_path):
    """The winner commits between the late request's status read and its insert."""

    async def run():
        engine = _concurrent_engine(tmp_path)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        day = resolve_puzzle_date(datetime(2026, 11, 13, 12, 0, tzinfo=UTC), "Africa/Tripoli")
        try:
            async with sessions() as db:
                db.add(
                    WordlePuzzle(
                        puzzle_date=day.isoformat(),
                        answer=CRANE,
                        source="fixture",
                        fetched_at="2026-11-13T12:00:00+00:00",
                        payload_hash="hash",
                    )
                )
                await db.commit()
            async with sessions() as db:
                game = await ensure_game(db, owner_id="owner-one", day=day)

            async def winner_finishes():
                # Same session the late request is using: the read snapshot has
                # already seen in_progress, then the winner commits terminal
                # state and its guess.
                await db.execute(
                    update(WordleGame)
                    .where(WordleGame.id == game.id)
                    .values(status="won", updated_at="2026-11-13T12:00:01+00:00")
                )
                db.add(
                    WordleGuess(
                        id="winner-guess",
                        game_id=game.id,
                        owner_id="owner-one",
                        attempt=1,
                        word=CRANE,
                        marks=["green"] * 5,
                        request_id="winner",
                        created_at="2026-11-13T12:00:01+00:00",
                    )
                )
                await db.commit()

            async with sessions() as db:
                with pytest.raises(WordleServiceError) as error:
                    await submit_guess(
                        db,
                        owner_id="owner-one",
                        day=day,
                        answer=CRANE,
                        guess="SLATE",
                        request_id="late",
                        _race_hook=winner_finishes,
                    )
            assert error.value.code == "WORDLE_GAME_OVER"

            async with sessions() as db:
                rows = (
                    await db.scalars(
                        select(WordleGuess).where(WordleGuess.game_id == game.id).order_by(WordleGuess.attempt)
                    )
                ).all()
                game_status = await db.scalar(select(WordleGame.status).where(WordleGame.id == game.id))
            assert [(row.attempt, row.word) for row in rows] == [(1, CRANE)]
            assert game_status == "won"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_duplicate_request_id_replays_after_the_game_is_terminal(tmp_path):
    async def run():
        engine = _concurrent_engine(tmp_path)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        day = resolve_puzzle_date(datetime(2026, 11, 13, 12, 0, tzinfo=UTC), "Africa/Tripoli")
        try:
            async with sessions() as db:
                db.add(
                    WordlePuzzle(
                        puzzle_date=day.isoformat(),
                        answer=CRANE,
                        source="fixture",
                        fetched_at="2026-11-13T12:00:00+00:00",
                        payload_hash="hash",
                    )
                )
                await db.commit()
            async with sessions() as db:
                await ensure_game(db, owner_id="owner-one", day=day)
                won = await submit_guess(
                    db,
                    owner_id="owner-one",
                    day=day,
                    answer=CRANE,
                    guess=CRANE,
                    request_id="win",
                )
            assert won.game.status == "won"
            async with sessions() as db:
                replay = await submit_guess(
                    db,
                    owner_id="owner-one",
                    day=day,
                    answer=CRANE,
                    guess=CRANE,
                    request_id="win",
                )
            assert replay.game.status == "won"
            assert [g.word for g in replay.game.guesses] == [CRANE]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_guess_early_exits_release_the_locked_game_transaction(tmp_path):
    async def run():
        engine = _concurrent_engine(tmp_path)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        day = resolve_puzzle_date(datetime(2026, 11, 13, 12, 0, tzinfo=UTC), "Africa/Tripoli")
        try:
            async with sessions() as db:
                db.add(
                    WordlePuzzle(
                        puzzle_date=day.isoformat(),
                        answer=CRANE,
                        source="fixture",
                        fetched_at="2026-11-13T12:00:00+00:00",
                        payload_hash="hash",
                    )
                )
                await db.commit()
            async with sessions() as db:
                await ensure_game(db, owner_id="owner-one", day=day)
                await submit_guess(db, owner_id="owner-one", day=day, answer=CRANE, guess=CRANE, request_id="win")

            # Replay of the winning id returns while releasing the lock.
            async with sessions() as db:
                replay = await submit_guess(
                    db, owner_id="owner-one", day=day, answer=CRANE, guess=CRANE, request_id="win"
                )
                assert replay.game.status == "won"
                assert db.in_transaction() is False

            # Request-id conflict releases the lock before raising.
            async with sessions() as db:
                with pytest.raises(WordleServiceError) as conflict:
                    await submit_guess(db, owner_id="owner-one", day=day, answer=CRANE, guess="SLATE", request_id="win")
                assert conflict.value.code == "WORDLE_REQUEST_CONFLICT"
                assert db.in_transaction() is False

            # Late guess on a terminal game releases the lock before raising.
            async with sessions() as db:
                with pytest.raises(WordleServiceError) as over:
                    await submit_guess(
                        db, owner_id="owner-one", day=day, answer=CRANE, guess="SLATE", request_id="late"
                    )
                assert over.value.code == "WORDLE_GAME_OVER"
                assert db.in_transaction() is False
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_concurrent_tab_submissions_do_not_double_count(tmp_path):
    async def run():
        engine = _concurrent_engine(tmp_path)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        day = resolve_puzzle_date(datetime(2026, 11, 13, 12, 0, tzinfo=UTC), "Africa/Tripoli")
        try:
            async with sessions() as db:
                db.add(
                    WordlePuzzle(
                        puzzle_date=day.isoformat(),
                        answer=CRANE,
                        source="fixture",
                        fetched_at="2026-11-13T12:00:00+00:00",
                        payload_hash="hash",
                    )
                )
                await db.commit()
            async with sessions() as db:
                await ensure_game(db, owner_id="owner-one", day=day)

            async def guess(request_id):
                async with sessions() as db:
                    return await submit_guess(
                        db,
                        owner_id="owner-one",
                        day=day,
                        answer=CRANE,
                        guess="SLATE",
                        request_id=request_id,
                    )

            first, second = await asyncio.gather(guess("tab-a"), guess("tab-b"))
            async with sessions() as db:
                rows = (await db.scalars(select(WordleGuess).where(WordleGuess.game_id == first.game.id))).all()
                game = await db.get(WordleGame, first.game.id)
            assert sorted(row.attempt for row in rows) == [1, 2]
            assert len({row.request_id for row in rows}) == 2
            assert game is not None and game.status == "in_progress"
            assert {first.game.id, second.game.id} == {game.id}
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_concurrent_same_request_id_consumes_one_attempt(tmp_path):
    async def run():
        engine = _concurrent_engine(tmp_path)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        day = resolve_puzzle_date(datetime(2026, 11, 13, 12, 0, tzinfo=UTC), "Africa/Tripoli")
        try:
            async with sessions() as db:
                db.add(
                    WordlePuzzle(
                        puzzle_date=day.isoformat(),
                        answer=CRANE,
                        source="fixture",
                        fetched_at="2026-11-13T12:00:00+00:00",
                        payload_hash="hash",
                    )
                )
                await db.commit()
            async with sessions() as db:
                await ensure_game(db, owner_id="owner-one", day=day)

            async def guess():
                async with sessions() as db:
                    return await submit_guess(
                        db,
                        owner_id="owner-one",
                        day=day,
                        answer=CRANE,
                        guess="SLATE",
                        request_id="same-tab",
                    )

            first, second = await asyncio.gather(guess(), guess())
            async with sessions() as db:
                rows = (
                    await db.scalars(
                        select(WordleGuess).where(
                            WordleGuess.owner_id == "owner-one", WordleGuess.request_id == "same-tab"
                        )
                    )
                ).all()
            assert len(rows) == 1
            assert first.marks == second.marks
            assert len(first.game.guesses) == len(second.game.guesses) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
