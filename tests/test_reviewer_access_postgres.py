"""PostgreSQL concurrency for reviewer admission limits and revocation."""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from app.services.reviewer_access import (
    CREATION_LIMIT,
    SOURCE_ATTEMPT_LIMIT,
    ReviewerAccessDenied,
    ReviewerLimitExceeded,
    admit,
    authorize,
    create_invitation,
    record_attempt,
    revoke_invitation,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("TEST_POSTGRES_URL")


async def _factory():
    engine = create_async_engine(URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL requires disposable migrated PostgreSQL")
def test_postgres_attempt_limit_is_exact_under_concurrency():
    async def run():
        engine, factory = await _factory()
        now = datetime(2032, 5, 6, 7, 8, tzinfo=UTC)
        minute = now.replace(second=0, microsecond=0).isoformat()
        source = "203.0.113.9"
        try:
            async with factory() as db:
                await db.execute(
                    text("DELETE FROM reviewer_attempt_windows WHERE minute = :minute"),
                    {"minute": minute},
                )
                await db.execute(
                    text("DELETE FROM reviewer_source_attempt_windows WHERE minute = :minute"),
                    {"minute": minute},
                )
                await db.commit()

            async def attempt(value):
                async with factory() as db:
                    try:
                        await record_attempt(db, now, value)
                        return True
                    except ReviewerLimitExceeded:
                        return False

            results = await asyncio.gather(*(attempt(source) for _ in range(SOURCE_ATTEMPT_LIMIT + 1)))
            assert sum(results) == SOURCE_ATTEMPT_LIMIT
            # A different source is unaffected by the noisy one.
            async with factory() as db:
                assert await record_attempt(db, now, "198.51.100.4") is None
            async with factory() as db:
                source_remaining = await db.scalar(
                    text(
                        "SELECT attempts FROM reviewer_source_attempt_windows "
                        "WHERE source = :source AND minute = :minute"
                    ),
                    {"source": source, "minute": minute},
                )
                global_remaining = await db.scalar(
                    text("SELECT attempts FROM reviewer_attempt_windows WHERE minute = :minute"),
                    {"minute": minute},
                )
            assert source_remaining == SOURCE_ATTEMPT_LIMIT
            assert global_remaining == SOURCE_ATTEMPT_LIMIT + 1
        finally:
            async with factory() as db:
                await db.execute(
                    text("DELETE FROM reviewer_attempt_windows WHERE minute = :minute"), {"minute": minute}
                )
                await db.execute(
                    text("DELETE FROM reviewer_source_attempt_windows WHERE minute = :minute"),
                    {"minute": minute},
                )
                await db.commit()
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL requires disposable migrated PostgreSQL")
def test_postgres_creation_limit_is_exact_under_concurrency():
    async def run():
        engine, factory = await _factory()
        now = datetime(2032, 6, 7, 8, 9, tzinfo=UTC)
        hour = now.replace(minute=0, second=0, microsecond=0).isoformat()
        invitation_id = None
        try:
            async with factory() as db:
                invitation, token = await create_invitation(db, expires_at=None, now=now, max_identities=CREATION_LIMIT)
                invitation_id = invitation.id
            async with factory() as db:
                await db.execute(
                    text("DELETE FROM reviewer_creation_windows WHERE invitation_id = :identifier"),
                    {"identifier": invitation_id},
                )
                await db.commit()

            async def create_one():
                async with factory() as db:
                    try:
                        admitted = await admit(db, "", token, now)
                        return admitted.identity.id
                    except (ReviewerLimitExceeded, ReviewerAccessDenied):
                        return None

            owners = await asyncio.gather(*(create_one() for _ in range(CREATION_LIMIT + 1)))
            created = [owner for owner in owners if owner is not None]
            assert len(created) == CREATION_LIMIT
            assert len(set(created)) == CREATION_LIMIT
            async with factory() as db:
                counter = await db.scalar(
                    text(
                        "SELECT created FROM reviewer_creation_windows WHERE invitation_id = :identifier AND hour = :hour"
                    ),
                    {"identifier": invitation_id, "hour": hour},
                )
                identities = await db.scalar(
                    text("SELECT COUNT(*) FROM identities WHERE invitation_id = :identifier"),
                    {"identifier": invitation_id},
                )
            assert counter == CREATION_LIMIT
            assert identities == CREATION_LIMIT
        finally:
            if invitation_id is not None:
                async with factory() as db:
                    await db.execute(
                        text("DELETE FROM identities WHERE invitation_id = :identifier"), {"identifier": invitation_id}
                    )
                    await db.execute(
                        text("DELETE FROM reviewer_creation_windows WHERE invitation_id = :identifier"),
                        {"identifier": invitation_id},
                    )
                    await db.execute(
                        text("DELETE FROM reviewer_invitations WHERE id = :identifier"), {"identifier": invitation_id}
                    )
                    await db.commit()
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL requires disposable migrated PostgreSQL")
def test_postgres_admission_racing_revocation_never_stays_authorized():
    async def run():
        engine, factory = await _factory()
        now = datetime(2032, 7, 8, 9, 10, tzinfo=UTC)
        invitation_ids = []
        owner_ids = []
        try:
            for _ in range(15):
                async with factory() as db:
                    invitation, token = await create_invitation(
                        db, expires_at=None, now=now, max_identities=CREATION_LIMIT
                    )
                    invitation_ids.append(invitation.id)
                async with factory() as admission_db, factory() as revoke_db:
                    admitted, revoked = await asyncio.gather(
                        admit(admission_db, str(uuid.uuid4()), token, now),
                        revoke_invitation(revoke_db, invitation.id, now),
                        return_exceptions=True,
                    )
                assert revoked is True
                if not isinstance(admitted, Exception):
                    owner_ids.append(admitted.identity.id)
                    async with factory() as db:
                        with pytest.raises(ReviewerAccessDenied):
                            await authorize(db, admitted.cookie_token, now)
        finally:
            async with factory() as db:
                if owner_ids:
                    await db.execute(text("DELETE FROM identities WHERE id = ANY(:owners)"), {"owners": owner_ids})
                if invitation_ids:
                    await db.execute(
                        text("DELETE FROM reviewer_creation_windows WHERE invitation_id = ANY(:identifiers)"),
                        {"identifiers": invitation_ids},
                    )
                    await db.execute(
                        text("DELETE FROM reviewer_invitations WHERE id = ANY(:identifiers)"),
                        {"identifiers": invitation_ids},
                    )
                await db.commit()
            await engine.dispose()

    asyncio.run(run())
