"""PostgreSQL lease recovery and fencing for dispatcher preparation."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.contracts import parse_action
from app.infrastructure.database import ContextOperation, Conversation, Identity
from app.services.actions import _dispatch_transaction, dispatch_action
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("TEST_MEMORY_POSTGRES_URL")


def identity(owner, request_id):
    return hashlib.sha256(("dispatch:" + owner + ":" + request_id).encode()).hexdigest()


def fingerprint(conversation, action):
    return hashlib.sha256(
        json.dumps(
            {"conversation_id": conversation, "expected_revision": 0, "action": action.model_dump(mode="json")},
            sort_keys=True,
        ).encode()
    ).hexdigest()


@pytest.mark.skipif(not URL, reason="TEST_MEMORY_POSTGRES_URL requires disposable migrated PostgreSQL")
def test_postgres_lease_resume_and_fencing():
    async def run():
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        owner, conversation = str(uuid4()), str(uuid4())
        action = parse_action({"kind": "ProposeContextExclusion", "query": "trip"})
        calls = []

        async def prepare(**kwargs):
            calls.append("prepare")
            return {"proposal_id": "fresh"}

        async def effect(**kwargs):
            calls.append("effect")
            return kwargs["prepared"]

        effect.prepare = prepare
        try:
            async with factory() as db:
                db.add(Identity(id=owner, token_hash=owner, expires_at="2030"))
                await db.flush()
                db.add(Conversation(id=conversation, owner_id=owner, title="lease", revision=0))
                await db.commit()
                db.add(
                    ContextOperation(
                        id="prep",
                        owner_id=owner,
                        conversation_id=conversation,
                        command_type="DispatchAction",
                        payload={
                            "fingerprint": fingerprint(conversation, action),
                            "prepared": {"proposal_id": "cached"},
                        },
                        idempotency_key=identity(owner, "r1"),
                        outcome="preparing",
                        lease_id="old",
                        lease_expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                        created_at="2026-11-12T00:00:00+00:00",
                    )
                )
                await db.commit()
                result = await dispatch_action(
                    db,
                    owner_id=owner,
                    conversation_id=conversation,
                    request_id="r1",
                    expected_revision=0,
                    action=action,
                    catalog=None,
                    handlers={"ProposeContextExclusion": effect},
                )
                assert result == {"proposal_id": "cached"}
                assert calls == ["effect"]
                row = await db.get(ContextOperation, "prep")
                assert row.outcome == "completed"
                assert row.lease_id is None
            async with factory() as db:
                db.add(
                    ContextOperation(
                        id="fenced",
                        owner_id=owner,
                        conversation_id=conversation,
                        command_type="DispatchAction",
                        payload={"fingerprint": fingerprint(conversation, action), "prepared": {"ok": True}},
                        idempotency_key=identity(owner, "r2"),
                        outcome="preparing",
                        lease_id="new",
                        lease_expires_at="2030",
                        created_at="2026-11-12T00:00:00+00:00",
                    )
                )
                await db.commit()
                prevented = []

                async def forbidden(**kwargs):
                    prevented.append("effect")
                    return {"ok": True}

                with pytest.raises(HTTPException) as error:
                    async with db.begin():
                        await _dispatch_transaction(
                            db,
                            owner_id=owner,
                            conversation_id=conversation,
                            request_id="r2",
                            expected_revision=0,
                            action=action,
                            catalog=None,
                            handlers={"ProposeContextExclusion": forbidden},
                            prepared={"ok": True},
                            prepared_operation_id="fenced",
                            preparation_lease="old",
                        )
                assert error.value.detail["code"] == "ACTION_LEASE_LOST"
                assert prevented == []
        finally:
            await engine.dispose()

    asyncio.run(run())
