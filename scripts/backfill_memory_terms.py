"""Index memories that have no ``memory_terms`` rows yet (the hosted backfill after migration 0020).

Offline SQL migration (``alembic upgrade --sql``) cannot run Python, so the DDL is applied first and this script
fills in the existing rows with the same tokenizer the app uses. Idempotent: only missing (memory, term) pairs are
inserted, and each batch re-checks the live status and existing terms so a concurrent delete or save is safe.
``--dry-run`` reports the count and writes nothing.

    DATABASE_URL=postgresql+asyncpg://... uv run python scripts/backfill_memory_terms.py [--dry-run]
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import Insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.infrastructure.database import Memory, MemoryTerm  # noqa: E402
from app.services.memory_erasure_lock import lock_owner_memories  # noqa: E402
from app.services.memory_terms import terms_of  # noqa: E402

BATCH = 500
# One INSERT binds three parameters per term row; PostgreSQL caps a statement at 32767, so insert in safe chunks.
INSERT_CHUNK = 1000
LIVE_STATUSES = ("active", "hidden", "superseded")


async def _before_batch_insert(connection: AsyncConnection, memory_ids: Sequence[str]) -> None:
    """Seam between reading a batch and its insert, so tests can simulate a concurrent write."""


async def _lock_owners(connection: AsyncConnection, owner_ids: set[str]) -> None:
    """Hold each owner's memory write/erase lock until this batch commits (a no-op outside PostgreSQL)."""
    if connection.dialect.name != "postgresql":
        return
    async with AsyncSession(bind=connection, expire_on_commit=False) as session:
        for owner_id in sorted(owner_ids):
            await lock_owner_memories(session, owner_id)


def _insert_ignoring_conflicts(connection: AsyncConnection, values: list[dict[str, str]]) -> Insert:
    """Insert the batch, keeping an existing (memory_id, term) row instead of aborting."""
    if connection.dialect.name == "postgresql":
        return postgresql_insert(MemoryTerm).values(values).on_conflict_do_nothing()
    if connection.dialect.name == "sqlite":
        return sqlite_insert(MemoryTerm).values(values).prefix_with("OR IGNORE")
    return MemoryTerm.__table__.insert().values(values)


async def _insert_terms(connection: AsyncConnection, values: list[dict[str, str]]) -> int:
    """Insert term rows in chunks, returning how many rows the driver reports as actually written."""
    written = 0
    for start in range(0, len(values), INSERT_CHUNK):
        chunk = values[start : start + INSERT_CHUNK]
        result = await connection.execute(_insert_ignoring_conflicts(connection, chunk))
        # INSERT ... ON CONFLICT DO NOTHING / OR IGNORE reports written rows; -1 means the driver is not counting.
        written += result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(chunk)
    return written


async def backfill(url: str, *, dry_run: bool) -> int:
    engine = create_async_engine(url)
    inserted = 0
    last_id: str | None = None
    try:
        while True:
            async with engine.begin() as connection:
                statement = (
                    select(Memory.id, Memory.owner_id, Memory.content)
                    .where(Memory.status.in_(LIVE_STATUSES))
                    .order_by(Memory.id)
                    .limit(BATCH)
                )
                if last_id is not None:
                    statement = statement.where(Memory.id > last_id)
                batch = (await connection.execute(statement)).all()
                if not batch:
                    break
                last_id = batch[-1][0]
                memory_ids = [memory_id for memory_id, _, _ in batch]
                if not dry_run:
                    await _lock_owners(connection, {owner_id for _, owner_id, _ in batch})
                await _before_batch_insert(connection, memory_ids)
                # Re-check inside the transaction: a batch read before a delete or an app save may be stale.
                live = set(
                    (
                        await connection.execute(
                            select(Memory.id).where(Memory.id.in_(memory_ids), Memory.status != "deleted")
                        )
                    ).scalars()
                )
                indexed = set(
                    (
                        await connection.execute(
                            select(MemoryTerm.memory_id).where(MemoryTerm.memory_id.in_(memory_ids)).distinct()
                        )
                    ).scalars()
                )
                values = [
                    {"memory_id": memory_id, "owner_id": owner_id, "term": term}
                    for memory_id, owner_id, content in batch
                    if memory_id in live and memory_id not in indexed
                    for term in sorted(terms_of(content))
                ]
                if dry_run:
                    inserted += len(values)
                elif values:
                    inserted += await _insert_terms(connection, values)
    finally:
        await engine.dispose()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    url = os.environ["DATABASE_URL"]
    count = asyncio.run(backfill(url, dry_run=args.dry_run))
    print(f"{'would insert' if args.dry_run else 'inserted'} {count} term rows")


if __name__ == "__main__":
    main()
