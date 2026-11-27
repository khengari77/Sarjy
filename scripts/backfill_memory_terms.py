"""Index memories that have no ``memory_terms`` rows yet (the hosted backfill after migration 0019).

Offline SQL migration (``alembic upgrade --sql``) cannot run Python, so the DDL is applied first and this script
fills in the existing rows with the same tokenizer the app uses. Idempotent: only missing (memory, term) pairs are
inserted. ``--dry-run`` reports the count and writes nothing.

    DATABASE_URL=postgresql+asyncpg://... uv run python scripts/backfill_memory_terms.py [--dry-run]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.infrastructure.database import Memory, MemoryTerm  # noqa: E402
from app.services.memory_terms import terms_of  # noqa: E402

BATCH = 500


async def backfill(url: str, *, dry_run: bool) -> int:
    engine = create_async_engine(url)
    inserted = 0
    try:
        async with engine.begin() as connection:
            existing = {
                (memory_id, term)
                for memory_id, term in (await connection.execute(select(MemoryTerm.memory_id, MemoryTerm.term))).all()
            }
            rows = (
                await connection.execute(
                    select(Memory.id, Memory.owner_id, Memory.content).where(
                        Memory.status.in_(("active", "hidden", "superseded"))
                    )
                )
            ).all()
            missing = [
                {"memory_id": memory_id, "owner_id": owner_id, "term": term}
                for memory_id, owner_id, content in rows
                for term in sorted(terms_of(content))
                if (memory_id, term) not in existing
            ]
            inserted = len(missing)
            if not dry_run:
                for start in range(0, len(missing), BATCH):
                    await connection.execute(MemoryTerm.__table__.insert(), missing[start : start + BATCH])
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
