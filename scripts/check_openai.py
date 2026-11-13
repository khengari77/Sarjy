"""One tiny paid smoke request, reserved against Sarjy's real daily ledger first."""

import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.config import Settings
from app.services.budget import reserve


async def main():
    settings = Settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine)() as db:
            try:
                allowance = await reserve(db, "openai")
            except ValueError as exc:
                reason = "DAILY_BUDGET_EXHAUSTED" if str(exc) == "DAILY_BUDGET_EXHAUSTED" else "BUDGET_SERVICE_FAILED"
                print({"check": "stopped", "reason": reason})
                return
        print(
            {
                "reserved_usd": allowance["reserved_units"] / 1_000_000,
                "remaining_usd": allowance["remaining_units"] / 1_000_000,
                "day": allowance["day"],
            }
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + settings.openai_api_key},
                json={
                    "model": "gpt-6-luna",
                    "input": "Reply only OK.",
                    "max_output_tokens": 32,
                    "reasoning": {"effort": "none"},
                    "service_tier": "default",
                    "store": False,
                },
            )
            print(
                {
                    "provider": "openai",
                    "model": "gpt-6-luna",
                    "status": response.status_code,
                    "success": response.is_success,
                }
            )
            if not response.is_success:
                print({"check": "stopped", "reason": "PROVIDER_HTTP_FAILED", "status": response.status_code})
                return
            try:
                payload = response.json()
                usage = payload.get("usage", {}) if isinstance(payload, dict) else None
                if not isinstance(usage, dict):
                    raise ValueError
                values = {name: usage.get(name) for name in ("input_tokens", "output_tokens", "total_tokens")}
                if any(value is not None and (type(value) is not int or value < 0) for value in values.values()):
                    raise ValueError
            except ValueError:
                print({"check": "stopped", "reason": "PROVIDER_RESPONSE_INVALID"})
                return
            print(values)
    except httpx.HTTPError:
        print({"check": "stopped", "reason": "PROVIDER_CONNECTION_FAILED"})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
