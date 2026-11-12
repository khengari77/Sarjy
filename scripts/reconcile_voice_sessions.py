"""Coordinator-only bounded cleanup. Defaults to a non-mutating dry run."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import Settings
from app.services.session_cleanup import reconcile


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=20)
    args = parser.parse_args()
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            result = await reconcile(db, settings, apply=args.apply, limit=args.limit)
            print(json.dumps(result))
            return bool(result["errors"])
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
