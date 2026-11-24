from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.domain.budget import (
    DEEPGRAM_DAILY_CHARACTERS,
    DEEPSEEK_DAILY_MICRO_USD,
    DEEPSEEK_REQUEST_MICRO_USD,
    ELEVENLABS_DAILY_CHARACTERS,
    JEV_DAILY_REQUESTS,
    OPENAI_DAILY_MICRO_USD,
    OPENAI_REQUEST_MICRO_USD,
    budget_day,
)
from app.infrastructure.database import DevelopmentBudget
from app.infrastructure.upsert import insert_or_increment

REQUEST_UNITS = {"openai": OPENAI_REQUEST_MICRO_USD, "deepseek": DEEPSEEK_REQUEST_MICRO_USD, "jev": 1}
DAILY_LIMITS = {
    "openai": OPENAI_DAILY_MICRO_USD,
    "deepseek": DEEPSEEK_DAILY_MICRO_USD,
    "elevenlabs": ELEVENLABS_DAILY_CHARACTERS,
    "deepgram_tts": DEEPGRAM_DAILY_CHARACTERS,
    "jev": JEV_DAILY_REQUESTS,
}
CHARACTER_PROVIDERS = frozenset({"elevenlabs", "deepgram_tts"})
UNIT_LABELS = {"elevenlabs": "characters", "deepgram_tts": "characters", "jev": "requests"}


async def reserve_in_transaction(
    db: AsyncSession,
    provider: str,
    characters: int = 0,
    *,
    now: datetime | None = None,
    units: int | None = None,
    limit: int | None = None,
) -> dict:
    if provider not in DAILY_LIMITS:
        raise ValueError("Unsupported development budget")
    if units is None:
        units = characters if provider in CHARACTER_PROVIDERS else REQUEST_UNITS[provider]
    if limit is None:
        limit = DAILY_LIMITS[provider]
    if units <= 0 or units > limit:
        raise ValueError("DAILY_BUDGET_EXHAUSTED")
    day = budget_day(now or datetime.now(UTC))
    total = await insert_or_increment(
        db,
        DevelopmentBudget,
        {"day": day, "provider": provider, "reserved_units": units},
        index_elements=["day", "provider"],
        column="reserved_units",
        amount=units,
        limit=limit,
    )
    if total is None:
        raise ValueError("DAILY_BUDGET_EXHAUSTED")
    return {
        "day": day,
        "provider": provider,
        "reserved_units": total,
        "remaining_units": limit - total,
        "unit": UNIT_LABELS.get(provider, "micro_usd"),
    }


async def reserve(
    db: AsyncSession,
    provider: str,
    characters: int = 0,
    *,
    now: datetime | None = None,
    units: int | None = None,
    limit: int | None = None,
) -> dict:
    """Compatibility boundary owning commit/rollback; use only a clean session."""
    try:
        result = await reserve_in_transaction(db, provider, characters, now=now, units=units, limit=limit)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def guarded_openai_request(db_or_factory, request):
    """Commit the shared reservation in a dedicated session before delivery.

    Accept a session factory, or an existing session solely to discover its engine.
    Never flush, commit or roll back the caller's business session. Delivery errors
    retain the committed reservation. The callable must not perform ledger writes.
    """
    if isinstance(db_or_factory, AsyncSession):
        bind = db_or_factory.bind
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        factory = async_sessionmaker(engine, expire_on_commit=False)
    else:
        factory = db_or_factory
    async with factory() as budget_db, budget_db.begin():
        await reserve_in_transaction(budget_db, "openai")
    return await request()


async def reserve_jev_request(factory, *, limit: int) -> bool:
    """Reserve one Jev request in its own committed session; never raises.

    ``False`` means denied (budget exhausted or the ledger is unavailable), in
    which case the caller must not send the request.
    """
    try:
        async with factory() as budget_db, budget_db.begin():
            await reserve_in_transaction(budget_db, "jev", units=1, limit=limit)
    except Exception:
        return False
    return True
