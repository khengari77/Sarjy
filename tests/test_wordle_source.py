"""Wordle domain rules, source validation and the exactly-one-fetch guarantee."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

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
    CLAIM_LEASE,
    RETRY_BUDGET_SECONDS,
    HttpWordleSource,
    WordleServiceError,
    WordleSourceError,
    ensure_puzzle,
    ensure_today,
    parse_source_payload,
    poll_delays,
    validate_claim_lease,
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
    not_published: bool = False

    async def fetch(self, puzzle_date: date):
        self.calls.append(puzzle_date.isoformat())
        if self.not_published:
            raise WordleSourceError("WORDLE_NOT_PUBLISHED")
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
                    timezone="America/New_York",
                    now=datetime(2026, 11, 13, 5, 30, tzinfo=UTC),
                )
            async with sessions() as db:
                after = await ensure_today(
                    db,
                    source=source,
                    timezone="America/New_York",
                    now=datetime(2026, 11, 14, 5, 30, tzinfo=UTC),
                )
            assert (before.puzzle_date, after.puzzle_date) == ("2026-11-13", "2026-11-14")
            assert source.calls == ["2026-11-13", "2026-11-14"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_today_uses_the_publisher_schedule_not_the_player_local_date(tmp_path):
    """At 03:30 Tripoli the NYT puzzle is still the previous US-Eastern date."""

    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(answers={}, default_answer="CRANE")
        # 2026-11-14T00:30Z: 03:30 in Africa/Tripoli, 19:30 on 2026-11-13 in New York.
        instant = datetime(2026, 11, 14, 0, 30, tzinfo=UTC)
        assert resolve_puzzle_date(instant, "Africa/Tripoli") == date(2026, 11, 14)
        assert resolve_puzzle_date(instant, "America/New_York") == date(2026, 11, 13)
        try:
            async with sessions() as db:
                puzzle = await ensure_today(db, source=source, timezone="America/New_York", now=instant)
            assert puzzle.puzzle_date == "2026-11-13"
            assert source.calls == ["2026-11-13"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_claim_lease_strictly_exceeds_the_retry_budget():
    assert timedelta(seconds=RETRY_BUDGET_SECONDS) < CLAIM_LEASE
    with pytest.raises(ValueError):
        validate_claim_lease(timedelta(seconds=RETRY_BUDGET_SECONDS), timedelta(seconds=RETRY_BUDGET_SECONDS))
    with pytest.raises(ValueError):
        validate_claim_lease(timedelta(seconds=1), timedelta(seconds=RETRY_BUDGET_SECONDS))


def test_wait_polling_uses_capped_exponential_backoff():
    delays = list(poll_delays(63.7, initial=0.05, maximum=1.0))
    # 0.05 + 0.1 + 0.2 + 0.4 + 0.8 = 1.55s, then ~1s polls.
    assert len(delays) < 80
    assert delays[:5] == [0.05, 0.1, 0.2, 0.4, 0.8]
    assert all(delay <= 1.0 for delay in delays)
    assert sum(delays) >= 63.7


def test_http_source_classifies_missing_vs_broken():
    def handler(request):
        path = request.url.path
        if path.endswith("2026-11-12.json"):
            return httpx.Response(404)
        if path.endswith("2026-11-13.json"):
            return httpx.Response(200, json={"solution": "CRANE", "print_date": "2026-11-12"})
        if path.endswith("2026-11-14.json"):
            return httpx.Response(200, json=["not", "a", "dict"])
        if path.endswith("2026-11-15.json"):
            return httpx.Response(200, json={"solution": "CRANE"})
        if path.endswith("2026-11-16.json"):
            return httpx.Response(200, json={"solution": "CRANE", "print_date": "not-a-date"})
        return httpx.Response(503)

    source = HttpWordleSource("https://wordle.example/v2/{date}.json", transport=httpx.MockTransport(handler))
    for day in (date(2026, 11, 12), date(2026, 11, 13)):
        with pytest.raises(WordleSourceError) as missing:
            asyncio.run(source.fetch(day))
        assert missing.value.code == "WORDLE_NOT_PUBLISHED"
    for day in (date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 16), date(2026, 11, 17)):
        with pytest.raises(WordleSourceError) as broken:
            asyncio.run(source.fetch(day))
        assert broken.value.code == "WORDLE_SOURCE_FAILED"


def test_released_claim_is_not_stealable_before_the_retry_cooldown(tmp_path):
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

            # Past the old 30s lease but inside the 60s retry cooldown: the
            # released row must not be reclaimed, so no new source call.
            leak = FIXED_NOW.replace(second=30)
            async with sessions() as db:
                with pytest.raises(WordleServiceError):
                    await ensure_puzzle(db, DAY, source=source, now=leak)
            assert source.calls == ["2026-11-13"] * 3
            async with sessions() as db:
                row = await db.scalar(select(WordlePuzzle).where(WordlePuzzle.puzzle_date == "2026-11-13"))
                assert row is not None and row.claim_id is None
                assert row.claim_expires_at is None
                assert row.retry_after is not None

            # After the cooldown the date may be retried.
            later = FIXED_NOW + timedelta(seconds=61)
            async with sessions() as db:
                with pytest.raises(WordleServiceError):
                    await ensure_puzzle(db, DAY, source=source, now=later)
            assert source.calls == ["2026-11-13"] * 6
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_stored_future_puzzle_is_never_served(tmp_path):
    """The ingested schedule is an upper bound; an early row must not leak."""

    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(not_published=True)
        try:
            async with sessions() as db:
                db.add(
                    WordlePuzzle(
                        puzzle_date="2026-11-15",
                        answer="CRANE",
                        source="fixture",
                        fetched_at=FIXED_NOW.isoformat(),
                        payload_hash="hash",
                    )
                )
                await db.commit()
            async with sessions() as db:
                with pytest.raises(WordleServiceError) as error:
                    await ensure_today(
                        db,
                        source=source,
                        timezone="America/New_York",
                        now=FIXED_NOW,
                    )
            assert error.value.code == "WORDLE_UNAVAILABLE"
            assert source.calls == ["2026-11-13"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_future_row_does_not_hide_an_older_valid_answer(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(not_published=True)
        try:
            async with sessions() as db:
                for day, answer in (("2026-11-15", "CRANE"), ("2026-11-13", "SLATE")):
                    db.add(
                        WordlePuzzle(
                            puzzle_date=day,
                            answer=answer,
                            source="fixture",
                            fetched_at=FIXED_NOW.isoformat(),
                            payload_hash="hash",
                        )
                    )
                await db.commit()
            async with sessions() as db:
                puzzle = await ensure_today(
                    db,
                    source=source,
                    timezone="America/New_York",
                    now=datetime(2026, 11, 14, 6, 0, tzinfo=UTC),
                )
            assert (puzzle.puzzle_date, puzzle.answer) == ("2026-11-13", "SLATE")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_not_published_serves_the_last_ingested_puzzle_degraded(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(not_published=True)
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
            # 2026-11-14T01:00 New York: the 14th should be live, but the
            # source has not published it yet.  Not-published is not retried.
            async with sessions() as db:
                puzzle = await ensure_today(
                    db,
                    source=source,
                    timezone="America/New_York",
                    now=datetime(2026, 11, 14, 6, 0, tzinfo=UTC),
                )
            assert puzzle.puzzle_date == "2026-11-13"
            assert puzzle.answer == "CRANE"
            # A second request inside the cooldown must also serve the same
            # degraded puzzle, not flip to WORDLE_UNAVAILABLE.
            async with sessions() as db:
                again = await ensure_today(
                    db,
                    source=source,
                    timezone="America/New_York",
                    now=datetime(2026, 11, 14, 6, 0, 1, tzinfo=UTC),
                )
            assert again.puzzle_date == "2026-11-13"
            assert again.answer == "CRANE"
            assert source.calls == ["2026-11-14"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_broken_source_does_not_serve_a_stale_playable_game(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(always_fail=True)
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
                with pytest.raises(WordleServiceError) as error:
                    await ensure_today(
                        db,
                        source=source,
                        timezone="America/New_York",
                        now=datetime(2026, 11, 14, 6, 0, tzinfo=UTC),
                    )
            assert error.value.code == "WORDLE_UNAVAILABLE"
            assert source.calls == ["2026-11-14"] * 3
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_slow_but_bounded_fetch_is_served_to_a_late_concurrent_waiter(tmp_path):
    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        started = asyncio.Event()

        class FiveSecondSource:
            label = "fixture://slow"
            calls = 0

            async def fetch(self, puzzle_date):
                self.calls += 1
                started.set()
                await asyncio.sleep(5)
                return parse_source_payload(
                    {"solution": "CRANE", "print_date": puzzle_date.isoformat()},
                    puzzle_date,
                    source=self.label,
                )

        source = FiveSecondSource()
        try:

            async def request():
                async with sessions() as db:
                    row = await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
                    return row.puzzle_date, row.answer

            first = asyncio.create_task(request())
            await asyncio.wait_for(started.wait(), timeout=5)
            # The second tab arrives after the fetch is already in flight; it
            # must wait for the stored answer instead of returning 503.
            second = asyncio.create_task(request())
            results = await asyncio.wait_for(asyncio.gather(first, second), timeout=15)
            assert results[0] == results[1] == ("2026-11-13", "CRANE")
            assert source.calls == 1
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_fetch_attempt_is_hard_bounded_by_the_total_timeout(tmp_path, monkeypatch):
    import app.services.wordle as wordle_module

    monkeypatch.setattr(wordle_module, "FETCH_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(wordle_module, "MAX_FETCH_ATTEMPTS", 2)
    monkeypatch.setattr(wordle_module, "BACKOFF_SECONDS", (0.0,))

    async def run():
        engine = _memory_engine(tmp_path)
        await _create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = FixtureSource(answers={}, default_answer="CRANE")
        source_fetch = source.fetch

        async def hanging_fetch(puzzle_date):
            source.calls.append(puzzle_date.isoformat())
            await asyncio.sleep(30)
            return await source_fetch(puzzle_date)

        source.fetch = hanging_fetch  # type: ignore[method-assign]
        try:
            async with sessions() as db:
                started = asyncio.get_running_loop().time()
                with pytest.raises(WordleServiceError):
                    await ensure_puzzle(db, DAY, source=source, now=FIXED_NOW)
                elapsed = asyncio.get_running_loop().time() - started
            assert source.calls == ["2026-11-13", "2026-11-13"]
            assert elapsed < 2
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
                assert row.last_attempt_at == FIXED_NOW.astimezone(UTC).isoformat(timespec="microseconds")
        finally:
            await engine.dispose()

    asyncio.run(run())
