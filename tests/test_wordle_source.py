"""Wordle domain rules, source validation and the exactly-one-fetch guarantee."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import httpx
import pytest
from app.domain.wordle import (
    WordleMark,
    WordleValidationError,
    normalize_guess,
    resolve_puzzle_date,
    score_guess,
    status_after_guess,
    validate_answer,
)
from app.infrastructure.database import Base, WordlePuzzle
from app.services.wordle import (
    HttpWordleSource,
    WordleServiceError,
    WordleSourceError,
    ensure_puzzle,
    ensure_today,
    parse_source_payload,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DAY = date(2026, 11, 13)
FIXED_NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@dataclass
class FixtureSource:
    answers: dict[str, str] = field(default_factory=lambda: {"2026-11-13": "CRANE"})
    default_answer: str = "CRANE"
    label: str = "fixture://wordle"
    calls: list[str] = field(default_factory=list)
    failures: int = 0
    always_fail: bool = False

    async def fetch(self, puzzle_date: date):
        self.calls.append(puzzle_date.isoformat())
        if self.always_fail or self.failures:
            if self.failures:
                self.failures -= 1
            raise WordleSourceError("WORDLE_SOURCE_FAILED")
        answer = self.answers.get(puzzle_date.isoformat(), self.default_answer)
        return parse_source_payload(
            {"solution": answer, "print_date": puzzle_date.isoformat()},
            puzzle_date,
            source=self.label,
        )


def test_duplicate_letter_scoring_matches_two_pass_rule():
    assert score_guess("ABBEY", "BABES") == (
        WordleMark.YELLOW,
        WordleMark.YELLOW,
        WordleMark.GREEN,
        WordleMark.GREEN,
        WordleMark.GRAY,
    )
    # One E in the answer is already a green; the earlier repeated E is gray.
    assert score_guess("CRANE", "EERIE") == (
        WordleMark.GRAY,
        WordleMark.GRAY,
        WordleMark.YELLOW,
        WordleMark.GRAY,
        WordleMark.GREEN,
    )
    assert score_guess("CRANE", "NACRE") == (
        WordleMark.YELLOW,
        WordleMark.YELLOW,
        WordleMark.YELLOW,
        WordleMark.YELLOW,
        WordleMark.GREEN,
    )


def test_answer_and_guess_validation():
    assert validate_answer("CRANE") == "CRANE"
    assert normalize_guess("  crane ") == "CRANE"
    for invalid in ("CRAN", "CRANES", "CR4NE", "crane", ""):
        with pytest.raises(WordleValidationError):
            validate_answer(invalid)
    with pytest.raises(WordleValidationError):
        normalize_guess("CR4NE")
    with pytest.raises(WordleValidationError):
        status_after_guess("CRANE", "CRANE", 0)


def test_status_after_six_attempts():
    assert status_after_guess("CRANE", "SLATE", 1) == "in_progress"
    assert status_after_guess("CRANE", "SLATE", 6) == "lost"
    assert status_after_guess("CRANE", "CRANE", 2) == "won"


def test_puzzle_date_rolls_over_in_configured_timezone():
    assert resolve_puzzle_date(datetime(2026, 11, 12, 22, 30, tzinfo=UTC), "Africa/Tripoli") == date(2026, 11, 13)
    assert resolve_puzzle_date(datetime(2026, 11, 13, 21, 59, tzinfo=UTC), "Africa/Tripoli") == date(2026, 11, 13)
    assert resolve_puzzle_date(datetime(2026, 11, 13, 22, 0, tzinfo=UTC), "Africa/Tripoli") == date(2026, 11, 14)
    assert resolve_puzzle_date(datetime(2026, 11, 13, 22, 0, tzinfo=UTC), "UTC") == date(2026, 11, 13)
    with pytest.raises(WordleValidationError):
        resolve_puzzle_date(FIXED_NOW, "Not/AZone")


def test_source_payload_rejects_wrong_date_and_shape():
    assert parse_source_payload({"solution": "CRANE", "print_date": "2026-11-13"}, DAY, source="fixture").answer == (
        "CRANE"
    )
    for payload in (
        {"solution": "CRANE", "print_date": "2026-11-12"},
        {"solution": "CRANE"},
        {"solution": "CRAN", "print_date": "2026-11-13"},
        {"solution": "crane", "print_date": "2026-11-13"},
        {"solution": 12345, "print_date": "2026-11-13"},
        ["CRANE"],
    ):
        with pytest.raises(WordleSourceError):
            parse_source_payload(payload, DAY, source="fixture")


def test_http_source_uses_configured_url_and_mock_transport():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"solution": "CRANE", "print_date": "2026-11-13"})

    source = HttpWordleSource(
        "https://wordle.example/v2/{date}.json",
        transport=httpx.MockTransport(handler),
    )
    fetched = asyncio.run(source.fetch(DAY))
    assert fetched.answer == "CRANE"
    assert seen == ["https://wordle.example/v2/2026-11-13.json"]


def _memory_engine(tmp_path):
    return create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/wordle-source.db",
        connect_args={"timeout": 30},
    )


async def _create_schema(engine):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


def test_concurrent_ensure_puzzle_makes_one_fetch_and_returns_same_row(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource()
        try:

            async def request():
                async with sessions() as db:
                    row = await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
                    return row.puzzle_date, row.answer, row.payload_hash

            results = await asyncio.gather(request(), request())
            assert results[0] == results[1]
            assert results[0][1] == "CRANE"
            assert source.calls == ["2026-11-13"]
            async with sessions() as db:
                rows = (await db.scalars(select(WordlePuzzle))).all()
                assert len(rows) == 1
                assert rows[0].claim_id is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_many_concurrent_requests_share_one_in_flight_fetch(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowSource:
            label = "fixture://slow"
            calls = 0

            async def fetch(self, puzzle_date):
                self.calls += 1
                started.set()
                await release.wait()
                return parse_source_payload(
                    {"solution": "CRANE", "print_date": puzzle_date.isoformat()},
                    puzzle_date,
                    source=self.label,
                )

        source = SlowSource()
        try:

            async def request():
                async with sessions() as db:
                    row = await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
                    return row.puzzle_date, row.answer, row.payload_hash

            tasks = [asyncio.create_task(request()) for _ in range(8)]
            await asyncio.wait_for(started.wait(), timeout=5)
            # Give the losers time to observe the claim before the source returns.
            await asyncio.sleep(0.2)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
            assert len(set(results)) == 1
            assert results[0][1] == "CRANE"
            assert source.calls == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_failed_fetch_starts_a_cooldown_before_the_next_source_call(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(always_fail=True)
        try:
            async with sessions() as db:
                with pytest.raises(WordleServiceError):
                    await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
            assert source.calls == ["2026-11-13"] * 3
            async with sessions() as db:
                with pytest.raises(WordleServiceError) as error:
                    await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
            assert error.value.code == "WORDLE_UNAVAILABLE"
            assert source.calls == ["2026-11-13"] * 3
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_existing_puzzle_row_skips_the_source(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource()
        try:
            async with sessions() as db:
                db.add(
                    WordlePuzzle(
                        puzzle_date="2026-11-13",
                        answer="CRANE",
                        source="fixture",
                        fetched_at=FIXED_NOW.isoformat(),
                        payload_hash="hash",
                    )
                )
                await db.commit()
            async with sessions() as db:
                row = await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
            assert row.answer == "CRANE"
            assert source.calls == []
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_ensure_today_resolves_timezone_and_rolls_over(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(answers={}, default_answer="CRANE")
        try:
            async with sessions() as db:
                before = await ensure_today(
                    db,
                    source=source,
                    timezone="Africa/Tripoli",
                    now=datetime(2026, 11, 12, 22, 30, tzinfo=UTC),
                )
            async with sessions() as db:
                after = await ensure_today(
                    db,
                    source=source,
                    timezone="Africa/Tripoli",
                    now=datetime(2026, 11, 13, 22, 30, tzinfo=UTC),
                )
            assert (before.puzzle_date, after.puzzle_date) == ("2026-11-13", "2026-11-14")
            assert source.calls == ["2026-11-13", "2026-11-14"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_failed_source_serves_unavailable_without_fabricating_an_answer(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(always_fail=True)
        try:
            async with sessions() as db:
                with pytest.raises(WordleServiceError) as error:
                    await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
            assert error.value.code == "WORDLE_UNAVAILABLE"
            assert source.calls == ["2026-11-13"] * 3
            async with sessions() as db:
                row = await db.scalar(select(WordlePuzzle).where(WordlePuzzle.puzzle_date == "2026-11-13"))
                assert row is not None
                assert row.answer is None
                assert row.fetched_at is None
                assert row.claim_id is None
                assert row.last_attempt_at == FIXED_NOW.isoformat()
        finally:
            await engine.dispose()

    asyncio.run(run())
