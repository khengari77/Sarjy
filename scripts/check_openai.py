"""One tiny paid smoke request, reserved against Sarjy's real daily ledger first."""

import asyncio
from pathlib import Path
import sys
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

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
            allowance = await reserve(db, "openai")
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
            if response.is_success:
                usage = response.json().get("usage", {})
                print({name: usage.get(name) for name in ["input_tokens", "output_tokens", "total_tokens"]})
            else:
                error = response.json().get("error", {})
                print({"error_code": error.get("code"), "error_type": error.get("type")})
    except (httpx.HTTPError, ValueError) as exc:
        print(
            {
                "check": "stopped",
                "reason": "DAILY_BUDGET_EXHAUSTED" if isinstance(exc, ValueError) else "PROVIDER_CONNECTION_FAILED",
            }
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
