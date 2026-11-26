"""Save a proposed fact without a spoken question, journal it, and undo it by id.

The model and the worker only ever *propose*. This module is the one place a proposal becomes a saved memory without
the user answering a question, and it only runs when the pure policy said ``save`` (``domain/memory_policy.py``). Every
change is journalled as a ``ContextOperation`` so it can be shown ("Remembered: ...") and undone by id.
"""

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import MemoryMode
from app.domain.memory_changes import AUTO_SAVE, MemoryChange, Revert, RowState, revert_decision
from app.domain.memory_policy import Decision, plan_for_proposal
from app.infrastructure.database import ContextOperation, Memory, now
from app.services.memory import MemoryService
from app.services.memory_capture import (
    MemoryCaptureError,
    confirm_memory,
    proposal_summary,
)
from app.services.memory_erasure_lock import lock_owner_memories
from app.services.memory_visibility import Erased, scrub_copies

APPLIED = "applied"
UNDONE = "undone"
LABEL_CHARS = 80


class ChangeNotFound(Exception):
    pass


class ChangeConflict(Exception):
    """Something touched the rows since the change, so undoing it would clobber newer state."""


def _label(prefix: str, text: str) -> str:
    snippet = " ".join(text.split())
    if len(snippet) > LABEL_CHARS:
        snippet = snippet[: LABEL_CHARS - 1].rstrip() + "…"
    return f"{prefix}: {snippet}"


def _change_payload(operation: ContextOperation) -> dict:
    payload = operation.payload or {}
    return {
        "id": operation.id,
        "kind": payload.get("kind", "memory_saved"),
        "label": payload.get("label", ""),
        "undoable": operation.outcome == APPLIED,
        "memory_id": payload.get("memory_id"),
    }


async def _journalled(db: AsyncSession, owner_id: str, key: str) -> ContextOperation | None:
    return await db.scalar(
        select(ContextOperation).where(ContextOperation.owner_id == owner_id, ContextOperation.idempotency_key == key)
    )


async def auto_commit_memory(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    token: str,
    owner_id: str,
    conversation_id: str,
    secret: str,
    mode: MemoryMode,
) -> dict:
    """Commit a signed proposal now. A conflicting earlier fact is replaced, never asked about.

    The pure policy is re-checked here, server-side: a worker that calls this for a credential, a sensitive fact the
    user did not ask to keep, or while memory is not ``auto`` is refused (``MEMORY_NEEDS_CONSENT``), whatever it was
    told to do.

    Idempotent per proposal: a replay returns the same change without writing twice. An exact duplicate of an
    already-saved fact is not a change and carries none.
    """
    confirmation_id, text, explicit, negated = proposal_summary(token, secret, owner_id, conversation_id)
    if plan_for_proposal(mode, text, explicit=explicit, negated=negated) is not Decision.SAVE:
        raise MemoryCaptureError("MEMORY_NEEDS_CONSENT", 409)
    key = f"memauto:{confirmation_id}"[:100]
    existing = await _journalled(db, owner_id, key)
    if existing is not None:
        return {"status": "saved", "change": _change_payload(existing)}

    kind = "memory_saved"
    try:
        result = await confirm_memory(
            db,
            memory_service,
            token=token,
            decision="save",
            owner_id=owner_id,
            conversation_id=conversation_id,
            source_turn_id=None,
            secret=secret,
        )
    except MemoryCaptureError as error:
        replacement = error.details.get("token")
        if error.code != "MEMORY_CLARIFICATION_REQUIRED" or not isinstance(replacement, str):
            raise
        # The user already has a different value for this subject. Auto mode replaces it; the journal keeps the
        # old row so undo can bring it back.
        kind = "memory_updated"
        result = await confirm_memory(
            db,
            memory_service,
            token=replacement,
            decision="replace",
            owner_id=owner_id,
            conversation_id=conversation_id,
            source_turn_id=None,
            secret=secret,
        )

    memory = result.get("memory")
    if result.get("status") == "already_saved" or not memory:
        return {"status": result.get("status", "saved"), "change": None}

    superseded_id = memory.get("supersedes_id")
    superseded_version = None
    if superseded_id:
        old = await db.get(Memory, superseded_id)
        superseded_version = old.version if old is not None else None
    # The journal quotes the fact. Write it under the owner lock, so a permanent delete either finished first (the
    # row is gone and the label stays empty) or waits for this commit and then scrubs the journal entry.
    await lock_owner_memories(db, owner_id)
    current = await db.scalar(
        select(Memory.status).where(Memory.id == memory["id"]).execution_options(populate_existing=True)
    )
    label = (
        _label("Updated" if kind == "memory_updated" else "Remembered", memory["content"])
        if current == "active"
        else ""
    )
    operation = ContextOperation(
        id=str(uuid4()),
        owner_id=owner_id,
        conversation_id=conversation_id,
        command_type=AUTO_SAVE,
        payload={
            "kind": kind,
            "label": label,
            "memory_id": memory["id"],
            "version": memory["version"],
            "superseded_id": superseded_id,
            "superseded_version": superseded_version,
        },
        idempotency_key=key,
        outcome=APPLIED,
    )
    db.add(operation)
    await db.commit()
    return {"status": result.get("status", "saved"), "change": _change_payload(operation)}


async def undo_change(db: AsyncSession, *, owner_id: str, change_id: str) -> dict:
    """Undo one auto-saved change by id. Idempotent; refuses (``ChangeConflict``) if the rows moved on."""
    await lock_owner_memories(db, owner_id)
    operation = await db.scalar(
        select(ContextOperation).where(
            ContextOperation.id == change_id,
            ContextOperation.owner_id == owner_id,
            ContextOperation.command_type == AUTO_SAVE,
        )
    )
    if operation is None:
        raise ChangeNotFound
    payload = operation.payload or {}
    if operation.outcome == UNDONE:
        return {**_change_payload(operation), "undoable": False, "changed": False}
    if operation.outcome != APPLIED or "version" not in payload:
        # Permanently deleted (the journal was scrubbed) or otherwise no longer an applied change: nothing to revert.
        raise ChangeConflict

    change = MemoryChange(
        memory_id=payload["memory_id"],
        version=payload["version"],
        superseded_id=payload.get("superseded_id"),
        superseded_version=payload.get("superseded_version"),
    )
    new = await db.get(Memory, change.memory_id)
    erased = Erased.of(new) if new is not None and new.owner_id == owner_id else None
    old = await db.get(Memory, change.superseded_id) if change.superseded_id else None
    decision = revert_decision(
        change,
        RowState(new.status, new.version) if new is not None and new.owner_id == owner_id else None,
        RowState(old.status, old.version) if old is not None and old.owner_id == owner_id else None,
        undone=False,
    )
    if decision is Revert.CONFLICT:
        raise ChangeConflict
    if old is not None and old.subject:
        # Restoring the replaced fact must not leave two active facts for one subject (a newer fact with the same
        # parsed value can have been saved beside the one being undone).
        clash = await db.scalar(
            select(Memory.id)
            .where(
                Memory.owner_id == owner_id,
                Memory.subject == old.subject,
                Memory.status == "active",
                Memory.id != change.memory_id,
            )
            .limit(1)
        )
        if clash is not None:
            raise ChangeConflict

    # Only the new row is withdrawn. The earlier fact it replaced (if any) is restored untouched, so this is not
    # ``delete_memory``, which wipes a whole correction lineage. Both writes are guarded on status as well as version
    # (replacing or deleting a row does not bump its version) and must each match exactly one row; otherwise the rows
    # moved on between the check above and now, and the whole undo is rolled back.
    withdrawn = await db.execute(
        update(Memory)
        .where(
            Memory.id == change.memory_id,
            Memory.owner_id == owner_id,
            Memory.version == change.version,
            Memory.status == "active",
        )
        .values(status="deleted", content="", subject=None, embedding=None, updated_at=now())
    )
    restored = None
    if change.superseded_id:
        restored = await db.execute(
            update(Memory)
            .where(
                Memory.id == change.superseded_id,
                Memory.owner_id == owner_id,
                Memory.version == change.superseded_version,
                Memory.status == "superseded",
            )
            .values(status="active", version=Memory.version + 1, updated_at=now())
        )
    if withdrawn.rowcount != 1 or (restored is not None and restored.rowcount != 1):
        await db.rollback()
        raise ChangeConflict
    if erased is not None:
        # The withdrawn fact must leave the proposal, decision and prompt copies too, or a later permanent delete
        # (which finds the row already tombstoned) could never reach them.
        await scrub_copies(db, owner_id, [erased])
    operation.outcome = UNDONE
    # The label quotes the fact the user just withdrew; keep the id for audit and drop the text.
    operation.payload = {"kind": payload.get("kind"), "label": "", "memory_id": change.memory_id, "scrubbed": True}
    await db.commit()
    return {**_change_payload(operation), "undoable": False, "changed": True}
