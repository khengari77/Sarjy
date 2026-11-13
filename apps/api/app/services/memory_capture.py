"""Consent-aware memory capture shell around the existing memory service."""

import asyncio
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context_operations import sign_proposal, verify_proposal
from app.domain.memory_capture import MemoryCapture, parse_capture
from app.infrastructure.database import Memory, MemoryWriteRequest, now
from app.infrastructure.upsert import insert_ignore
from app.services.memory import MemoryService, memory_result

CAPTURE_KIND = "memory_capture"
PROPOSAL_TTL_SECONDS = 600
_SUBJECT_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _subject_lock(owner_id: str, subject: str | None) -> asyncio.Lock:
    key = (owner_id, subject or "explicit")
    lock = _SUBJECT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SUBJECT_LOCKS[key] = lock
    return lock


class MemoryCaptureError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def _request_key(owner_id: str, source_turn_id: str, content: str) -> str:
    digest = hashlib.sha256(f"{owner_id}|{source_turn_id}|{content.casefold()}".encode()).hexdigest()
    return f"memcap:{digest[:80]}"


def propose_memory(text: str, *, owner_id: str, conversation_id: str, source_turn_id: str, secret: str) -> dict | None:
    """Return a signed, read-only proposal or None when the text is not a fact."""
    capture = parse_capture(text)
    if capture is None:
        return None
    if not secret:
        raise MemoryCaptureError("MEMORY_CAPTURE_NOT_CONFIGURED", 503)
    token = sign_proposal(
        {
            "v": 1,
            "kind": CAPTURE_KIND,
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "source_turn_id": source_turn_id,
            "content": capture.text,
            "subject": capture.subject,
            "value": capture.value,
            "explicit": capture.explicit,
            "replaces": capture.replaces,
            "negated": capture.negated,
            "exp": int((datetime.now(UTC) + timedelta(seconds=PROPOSAL_TTL_SECONDS)).timestamp()),
        },
        secret,
    )
    return {"token": token, "text": capture.text, "explicit": capture.explicit}


def _verified(token: str, secret: str, owner_id: str, conversation_id: str) -> MemoryCapture:
    try:
        payload = verify_proposal(token, secret)
    except ValueError as exc:
        raise MemoryCaptureError("PROPOSAL_INVALID", 400) from exc
    if (
        payload.get("kind") != CAPTURE_KIND
        or payload.get("owner_id") != owner_id
        or payload.get("conversation_id") != conversation_id
    ):
        raise MemoryCaptureError("PROPOSAL_INVALID", 404)
    content = payload.get("content")
    value = payload.get("value")
    subject = payload.get("subject")
    explicit = payload.get("explicit", False)
    replaces = payload.get("replaces", False)
    negated = payload.get("negated", False)
    expires = payload.get("exp")
    if not isinstance(content, str) or not isinstance(value, str) or not content:
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    if subject is not None and not isinstance(subject, str):
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    if not isinstance(explicit, bool) or not isinstance(replaces, bool) or not isinstance(negated, bool):
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    if not isinstance(expires, int) or expires < int(datetime.now(UTC).timestamp()):
        raise MemoryCaptureError("PROPOSAL_EXPIRED", 409)
    # The signed payload is the authority. Re-parsing the canonical text here
    # breaks explicit remembering whose payload is not itself a first-person
    # sentence (for example ``Remember to buy milk`` or a quoted phrase).
    return MemoryCapture(
        text=content, subject=subject, value=value, explicit=explicit, replaces=replaces, negated=negated
    )


async def _exact_content(
    db: AsyncSession,
    *,
    owner_id: str,
    capture: MemoryCapture,
    status: str | None = None,
    source_turn_id: str | None = None,
) -> Memory | None:
    query = select(Memory).where(Memory.owner_id == owner_id, func.lower(Memory.content) == capture.text.casefold())
    if status is not None:
        query = query.where(Memory.status == status)
    if source_turn_id is not None:
        query = query.where(Memory.source_turn_id == source_turn_id)
    return await db.scalar(query.order_by(Memory.updated_at.desc()).limit(1))


async def _contradictions(db: AsyncSession, *, owner_id: str, capture: MemoryCapture) -> list[Memory]:
    if capture.subject is None:
        return []
    rows = (
        await db.scalars(
            select(Memory)
            .where(Memory.owner_id == owner_id, Memory.status == "active")
            .order_by(Memory.updated_at.desc(), Memory.id)
        )
    ).all()
    matches: list[Memory] = []
    for row in rows:
        existing = parse_capture(row.content)
        if existing is not None and existing.subject == capture.subject:
            matches.append(row)
    return matches


async def confirm_memory(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    token: str,
    decision: str,
    owner_id: str,
    conversation_id: str,
    source_turn_id: str | None,
    secret: str,
) -> dict:
    """Apply a signed proposal only after explicit save consent."""
    if not secret:
        raise MemoryCaptureError("MEMORY_CAPTURE_NOT_CONFIGURED", 503)
    capture = _verified(token, secret, owner_id, conversation_id)
    try:
        payload = verify_proposal(token, secret)
    except ValueError as exc:  # pragma: no cover - _verified already checked
        raise MemoryCaptureError("PROPOSAL_INVALID", 400) from exc
    proposed_turn = payload.get("source_turn_id")
    if not isinstance(proposed_turn, str) or not proposed_turn:
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    if decision == "discard":
        await _record_discard(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            proposed_turn=proposed_turn,
            capture=capture,
        )
        return {"status": "discarded", "memory": None}
    if decision not in {"save", "replace"}:
        raise MemoryCaptureError("INVALID_DECISION", 422)

    return await _apply_consent(
        db,
        memory_service,
        owner_id=owner_id,
        conversation_id=conversation_id,
        proposed_turn=proposed_turn,
        source_turn_id=source_turn_id,
        capture=capture,
        force_replace=decision == "replace",
    )


async def _apply_consent(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    source_turn_id: str | None,
    capture: MemoryCapture,
    force_replace: bool,
) -> dict:
    async with _subject_lock(owner_id, capture.subject):
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            # Cross-process serialization for the same owner/subject decision.
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"memcap:{owner_id}:{capture.subject or capture.text}"},
            )
        return await _write_consented(
            db,
            memory_service,
            owner_id=owner_id,
            conversation_id=conversation_id,
            proposed_turn=proposed_turn,
            source_turn_id=source_turn_id,
            capture=capture,
            force_replace=force_replace,
        )


async def _write_consented(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    source_turn_id: str | None,
    capture: MemoryCapture,
    force_replace: bool,
) -> dict:
    key = _request_key(owner_id, proposed_turn, capture.text)
    active_same = await _exact_content(db, owner_id=owner_id, capture=capture, status="active")
    if active_same is not None:
        return {"status": "already_saved", "memory": asdict(memory_result(active_same))}
    replay = await _exact_content(db, owner_id=owner_id, capture=capture, source_turn_id=proposed_turn)
    if replay is not None:
        return {"status": "already_saved", "memory": asdict(memory_result(replay))}
    superseded_same = await _exact_content(db, owner_id=owner_id, capture=capture, status="superseded")
    previous = await _contradictions(db, owner_id=owner_id, capture=capture)
    conflicting = [
        row
        for row in previous
        if (parsed := parse_capture(row.content)) is not None
        and (parsed.value != capture.value or parsed.negated != capture.negated)
    ]
    if len(conflicting) > 1:
        # More than one active row matches this slot. Picking one silently would
        # leave contradictory active facts behind, so require an explicit human
        # decision instead of guessing.
        raise MemoryCaptureError("MEMORY_CLARIFICATION_REQUIRED", 409)
    if conflicting:
        corrective = force_replace or capture.replaces or superseded_same is not None
        if not corrective:
            raise MemoryCaptureError("MEMORY_CLARIFICATION_REQUIRED", 409)
        row = conflicting[0]
        try:
            result = await memory_service.correct_memory(
                owner_id,
                row.id,
                row.version,
                capture.text,
                key,
                source_turn_id=source_turn_id or proposed_turn,
                conversation_id=conversation_id,
            )
        except HTTPException as exc:
            if (
                exc.status_code == 409
                and isinstance(exc.detail, dict)
                and exc.detail.get("code") == "MEMORY_VERSION_CONFLICT"
            ):
                # Do not leave a terminal failed claim for a race; allow the
                # signed proposal to be retried after clarification.
                await db.execute(
                    delete(MemoryWriteRequest).where(
                        MemoryWriteRequest.owner_id == owner_id, MemoryWriteRequest.request_key == key
                    )
                )
                await db.commit()
                raise MemoryCaptureError("MEMORY_CONFLICT_RETRY", 409) from exc
            raise
        return {"status": "corrected", "memory": asdict(result)}
    result = await memory_service.create_memory(
        owner_id, conversation_id, source_turn_id or proposed_turn, capture.text, key
    )
    return {"status": "saved", "memory": asdict(result)}


async def _record_discard(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    capture: MemoryCapture,
) -> None:
    """Make a decline durable so a later save replay cannot write the fact."""
    key = _request_key(owner_id, proposed_turn, capture.text)
    fingerprint = hashlib.sha256(f"discard|{proposed_turn}|{capture.text.casefold()}".encode()).hexdigest()
    await insert_ignore(
        db,
        MemoryWriteRequest,
        {
            "id": str(uuid4()),
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "request_key": key,
            "fingerprint": fingerprint,
            "state": "completed",
            "result": {"status": "discarded"},
            "created_at": now(),
            "updated_at": now(),
        },
        index_elements=["owner_id", "request_key"],
    )
    await db.commit()


__all__ = [
    "MemoryCaptureError",
    "confirm_memory",
    "propose_memory",
]
