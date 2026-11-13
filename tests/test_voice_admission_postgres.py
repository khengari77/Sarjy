"""Concurrent identical voice admissions on disposable PostgreSQL."""

import asyncio
import os

import httpx
import pytest
from app.config import Settings
from app.infrastructure.database import VoiceAdmission
from app.main import create_app
from sqlalchemy.ext.asyncio import create_async_engine
from test_session_lifecycle import AGENT_SECRET, FakeCatalog, FakeLiveKitAPI


def test_postgres_concurrent_identical_posts_create_one_admission_and_dispatch():
    url = os.environ.get("TEST_MEMORY_POSTGRES_URL")
    if not url:
        pytest.skip("Disposable PostgreSQL required")

    async def run():
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(VoiceAdmission.__table__.create, checkfirst=True)
        await engine.dispose()

        FakeLiveKitAPI.state = {"dispatched": [], "deleted": [], "dispatch_fails": False, "delete_fails": False}
        import livekit.api as livekit_api

        original = livekit_api.LiveKitAPI
        livekit_api.LiveKitAPI = FakeLiveKitAPI
        settings = Settings(
            database_url=url,
            openai_api_key="test-openai",
            deepgram_api_key="test-deepgram",
            eleven_api_key="test-eleven",
            default_llm_provider="openai_direct",
            livekit_url="https://fake.livekit",
            livekit_api_key="key" * 8,
            livekit_api_secret="secret" * 8,
            agent_service_secret=AGENT_SECRET,
            demo_session_budget=10,
            _env_file=None,
        )
        app = create_app(settings, voice_catalog=FakeCatalog())
        key = "postgres-concurrent-key"
        chat = None
        try:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    await client.post("/v1/demo-session")
                    chat = (await client.post("/v1/conversations")).json()["id"]
                    body = {"conversation_id": chat, "voice_id": "test-voice", "request_key": key}
                    first, second = await asyncio.gather(
                        client.post("/v1/voice/sessions", json=body),
                        client.post("/v1/voice/sessions", json=body),
                    )
                    assert {first.status_code, second.status_code} <= {201, 409}, (
                        first.status_code,
                        first.text,
                        second.status_code,
                        second.text,
                    )
                    winners = [response for response in (first, second) if response.status_code == 201]
                    assert winners, (first.text, second.text)
                    assert len({response.json()["id"] for response in winners}) == 1
                    for response in (first, second):
                        if response.status_code == 409:
                            assert response.json()["detail"]["code"] == "VOICE_ADMISSION_IN_PROGRESS"

                    retried = await client.post("/v1/voice/sessions", json=body)
                    assert retried.status_code == 201, retried.text
                    assert retried.json()["id"] == winners[0].json()["id"]
                    assert len(FakeLiveKitAPI.state["dispatched"]) == 1
        finally:
            import asyncpg

            plain = url.replace("postgresql+asyncpg://", "postgresql://")
            connection = await asyncpg.connect(plain)
            try:
                owner = None
                if chat is not None:
                    owner = await connection.fetchval("SELECT owner_id FROM conversations WHERE id=$1", chat)
                    await connection.execute("DELETE FROM voice_admissions WHERE request_key=$1", key)
                    await connection.execute("DELETE FROM voice_sessions WHERE conversation_id=$1", chat)
                    await connection.execute("DELETE FROM conversations WHERE id=$1", chat)
                else:
                    await connection.execute("DELETE FROM voice_admissions WHERE request_key=$1", key)
                if owner:
                    await connection.execute("DELETE FROM identities WHERE id=$1", owner)
            finally:
                await connection.close()
                livekit_api.LiveKitAPI = original

    asyncio.run(run())
