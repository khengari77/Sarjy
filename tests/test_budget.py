import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from app.config import Settings
from app.domain.budget import budget_day, reserve_units
from app.infrastructure.database import Base, DevelopmentBudget
from app.main import create_app
from app.services.budget import reserve
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@contextmanager
def capture_statements(engine):
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


@given(st.integers(min_value=0, max_value=1_000_000), st.integers(min_value=1, max_value=1_000_000))
def test_reservations_never_exceed_limit(used, requested):
    if used + requested <= 1_000_000:
        assert reserve_units(used, requested, 1_000_000) == used + requested
    else:
        with pytest.raises(ValueError, match="DAILY_BUDGET_EXHAUSTED"):
            reserve_units(used, requested, 1_000_000)


def test_budget_day_is_tripoli_calendar_day():
    assert budget_day(datetime(2026, 11, 11, 21, 59, tzinfo=UTC)) == "2026-11-11"
    assert budget_day(datetime(2026, 11, 11, 22, 0, tzinfo=UTC)) == "2026-11-12"


def test_openai_and_elevenlabs_are_separate_and_survive_restart(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/budget.db",
        agent_service_secret="test",
        elevenlabs_daily_characters=500,
        _env_file=None,
    )
    headers = {"x-agent-secret": "test"}
    with TestClient(create_app(settings)) as client:
        assert client.post("/internal/development/reserve", json={"provider": "openai"}).status_code == 401
        for _ in range(50):
            assert (
                client.post("/internal/development/reserve", headers=headers, json={"provider": "openai"}).status_code
                == 200
            )
        assert (
            client.post("/internal/development/reserve", headers=headers, json={"provider": "openai"}).status_code
            == 429
        )
        assert (
            client.post(
                "/internal/development/reserve", headers=headers, json={"provider": "elevenlabs", "characters": 500}
            ).status_code
            == 200
        )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post("/internal/development/reserve", headers=headers, json={"provider": "openai"}).status_code
            == 429
        )
        assert (
            client.post(
                "/internal/development/reserve", headers=headers, json={"provider": "elevenlabs", "characters": 1}
            ).status_code
            == 429
        )


def test_concurrent_reservations_and_next_day_reset(tmp_path):
    async def check():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/concurrent.db")
        sessions = async_sessionmaker(engine)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async def attempt():
            async with sessions() as db:
                try:
                    await reserve(db, "elevenlabs", 300, now=datetime(2026, 11, 11, 12, tzinfo=UTC))
                    return True
                except ValueError:
                    return False

        assert sorted(await asyncio.gather(attempt(), attempt())) == [False, True]
        async with sessions() as db:
            result = await reserve(db, "elevenlabs", 500, now=datetime(2026, 11, 12, 12, tzinfo=UTC))
            assert result["remaining_units"] == 0
        await engine.dispose()

    asyncio.run(check())


def test_successful_reservation_is_one_statement(tmp_path):
    async def check():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/one-statement.db")
        sessions = async_sessionmaker(engine)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with sessions() as db:
                with capture_statements(engine) as statements:
                    result = await reserve(db, "jev", units=1, limit=2)
                assert len(statements) == 1
                assert "ON CONFLICT (day, provider) DO UPDATE" in statements[0]
                assert set(result) == {"day", "provider", "reserved_units", "remaining_units", "unit"}
                assert result["provider"] == "jev"
                assert result["reserved_units"] == 1
                assert result["remaining_units"] == 1
                assert result["unit"] == "requests"
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_refused_reservation_leaves_reserved_units_unchanged(tmp_path):
    async def check():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/refused.db")
        sessions = async_sessionmaker(engine)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with sessions() as db:
                await reserve(db, "jev", units=2, limit=2)
                with (
                    capture_statements(engine) as statements,
                    pytest.raises(ValueError, match="DAILY_BUDGET_EXHAUSTED"),
                ):
                    await reserve(db, "jev", units=1, limit=2)
                assert len(statements) == 1
            async with sessions() as db:
                assert await db.scalar(select(DevelopmentBudget.reserved_units)) == 2
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_sequential_reservations_accumulate(tmp_path):
    async def check():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/accumulate.db")
        sessions = async_sessionmaker(engine)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with sessions() as db:
                assert (await reserve(db, "jev", units=1, limit=3))["reserved_units"] == 1
                second = await reserve(db, "jev", units=2, limit=3)
                assert second["reserved_units"] == 3
                assert second["remaining_units"] == 0
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_exact_limit_reservation_succeeds_and_one_unit_over_is_refused(tmp_path):
    async def check():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/limit.db")
        sessions = async_sessionmaker(engine)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with sessions() as db:
                with pytest.raises(ValueError, match="DAILY_BUDGET_EXHAUSTED"):
                    await reserve(db, "jev", units=3, limit=2)
                assert await db.scalar(select(DevelopmentBudget.reserved_units)) is None
                exact = await reserve(db, "jev", units=2, limit=2)
                assert exact["reserved_units"] == 2
                assert exact["remaining_units"] == 0
                with pytest.raises(ValueError, match="DAILY_BUDGET_EXHAUSTED"):
                    await reserve(db, "jev", units=1, limit=2)
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_settings_boot_with_eleven_key_and_no_voice_ids():
    # Voice is a per-session user setting (#148): no voice env constant is required to boot.
    settings = Settings(eleven_api_key="k", approved_voice_ids="", default_voice_id="", _env_file=None)
    assert settings.eleven_api_key == "k"


def test_malformed_voice_allow_list_still_fails_at_boot():
    with pytest.raises(ValueError):
        Settings(approved_voice_ids="not a valid id!", _env_file=None)


def test_elevenlabs_daily_characters_is_configurable(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/limit.db",
        agent_service_secret="test",
        elevenlabs_daily_characters=700,
        _env_file=None,
    )
    headers = {"x-agent-secret": "test"}
    url = "/internal/development/reserve"
    with TestClient(create_app(settings)) as client:
        assert client.post(url, headers=headers, json={"provider": "elevenlabs", "characters": 500}).status_code == 200
        assert client.post(url, headers=headers, json={"provider": "elevenlabs", "characters": 201}).status_code == 429
        assert client.post(url, headers=headers, json={"provider": "elevenlabs", "characters": 200}).status_code == 200


def test_elevenlabs_default_allowance_is_2500():
    assert Settings(_env_file=None).elevenlabs_daily_characters == 2500


def test_insert_or_increment_refuses_an_out_of_range_amount_without_touching_the_database(tmp_path):
    from app.infrastructure.database import DevelopmentBudget
    from app.infrastructure.upsert import insert_or_increment

    async def check():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bounds.db")
        sessions = async_sessionmaker(engine)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with sessions() as db:
                with capture_statements(engine) as statements:
                    for amount in (0, -1, 11):
                        refused = await insert_or_increment(
                            db,
                            DevelopmentBudget,
                            {"day": "2026-11-24", "provider": "jev", "reserved_units": amount},
                            index_elements=["day", "provider"],
                            column="reserved_units",
                            amount=amount,
                            limit=10,
                        )
                        assert refused is None
                assert statements == []  # a brand-new row is bounded by the helper, not only by its callers
        finally:
            await engine.dispose()

    asyncio.run(check())
