"""Adapters from the D2.7 service to the #7 dispatcher handler shape.

Published dispatcher interface: handlers are async callables registered at
``app.state.action_handlers[action.kind]`` and receive ``db``, ``owner_id``,
``conversation_id``, ``request_id``, ``expected_revision`` and ``action`` by
keyword.  The dispatcher owns the single commit; these handlers pass
``commit=False`` for the mutating Undo path.

The dispatcher module is not present on this branch, so registration is wired
defensively in ``main.py`` and this integration remains unverified until #7
merges.
"""

from app.services.context_operations import (
    MemorySearcher,
    create_proposal,
    keyword_search,
    undo_operation_in_transaction,
)


def build_context_action_handlers(*, secret: str, search: MemorySearcher = keyword_search) -> dict:
    async def prepare_search(*, db, owner_id, conversation_id, request_id, expected_revision, action):
        # Read-only preview: semantic search results with keep-exceptions.
        return await create_proposal(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            query=action.query,
            keep=tuple(getattr(action, "keep", ())),
            limit=getattr(action, "limit", 20),
            secret=secret,
            search=search,
        )

    async def search_memory(*, db, owner_id, conversation_id, request_id, expected_revision, action, prepared):
        from fastapi import HTTPException

        from app.infrastructure.database import Conversation

        conversation = await db.get(Conversation, conversation_id)
        if conversation.revision != prepared["revision"] or conversation.revision != expected_revision:
            raise HTTPException(409, detail={"code": "REVISION_CONFLICT"})
        return prepared

    search_memory.prepare = prepare_search

    async def restore_operation(*, db, owner_id, conversation_id, request_id, expected_revision, action):
        # Undo is revision-guarded; request_id supplies durable idempotency.
        return await undo_operation_in_transaction(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            operation_id=action.operation_id,
            expected_revision=expected_revision,
            idempotency_key=request_id,
        )

    return {
        "SearchMemory": search_memory,
        "ProposeContextExclusion": search_memory,
        "RestoreContextOperation": restore_operation,
    }
