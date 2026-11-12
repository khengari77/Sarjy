"""Public context-operation history excludes dispatcher bookkeeping."""

import asyncio

from app.infrastructure.database import ContextOperation
from app.services.context_operations import list_operations
from test_memory import setup


def test_operation_history_omits_dispatcher_claims(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path / "operations.db")
        try:
            async with sessions() as db:
                db.add_all(
                    [
                        ContextOperation(
                            id="prep",
                            owner_id="alice",
                            conversation_id="a1",
                            command_type="DispatchAction",
                            idempotency_key="prep-key",
                            outcome="preparing",
                            payload={"fingerprint": "x"},
                            created_at="2026-11-12T10:00:00+00:00",
                        ),
                        ContextOperation(
                            id="done",
                            owner_id="alice",
                            conversation_id="a1",
                            command_type="DispatchAction",
                            idempotency_key="done-key",
                            outcome="completed",
                            payload={"fingerprint": "y"},
                            created_at="2026-11-12T10:01:00+00:00",
                        ),
                        ContextOperation(
                            id="exclude",
                            owner_id="alice",
                            conversation_id="a1",
                            command_type="exclude",
                            idempotency_key="exclude-key",
                            outcome="applied",
                            payload={"diff": {"removed": ["m1"]}},
                            revision_before=0,
                            revision_after=1,
                            created_at="2026-11-12T10:02:00+00:00",
                        ),
                        ContextOperation(
                            id="restore",
                            owner_id="alice",
                            conversation_id="a1",
                            command_type="restore",
                            idempotency_key="restore-key",
                            outcome="applied",
                            payload={"diff": {"added": ["m1"]}},
                            revision_before=1,
                            revision_after=2,
                            created_at="2026-11-12T10:03:00+00:00",
                        ),
                    ]
                )
                await db.commit()
            async with sessions() as db:
                result = await list_operations(db, owner_id="alice", conversation_id="a1")
                ids = [operation["id"] for operation in result["operations"]]
                assert ids == ["restore", "exclude"]
        finally:
            await engine.dispose()

    asyncio.run(run())
