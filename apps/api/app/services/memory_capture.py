"""Consent-aware memory capture shell around the existing memory service."""

import hashlib
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context_operations import sign_proposal, verify_proposal
from app.domain.memory_capture import MemoryCapture, parse_capture
from app.infrastructure.database import Memory
from app.services.memory import MemoryService, memory_result

CAPTURE_KIND = "memory_capture"
MAX_CONTRADICTION_SCAN = 200


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
    if not isinstance(content, str) or not isinstance(value, str) or not content:
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    capture = parse_capture(content)
    if capture is None or capture.value != value:
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    return capture


async def _contradictions(db: AsyncSession, *, owner_id: str, capture: MemoryCapture) -> list[Memory]:
    if capture.subject is None:
        return []
    rows = (
        await db.scalars(
            select(Memory)
            .where(Memory.owner_id == owner_id, Memory.status == "active")
            .order_by(Memory.updated_at.desc(), Memory.id)
            .limit(MAX_CONTRADICTION_SCAN)
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
        return {"status": "discarded", "memory": None}
    if decision != "save":
        raise MemoryCaptureError("INVALID_DECISION", 422)

    key = _request_key(owner_id, proposed_turn, capture.text)
    previous = await _contradictions(db, owner_id=owner_id, capture=capture)
    same = [
        row
        for row in previous
        if (existing := parse_capture(row.content)) is not None and existing.value == capture.value
    ]
    if same:
        return {"status": "already_saved", "memory": asdict(memory_result(same[0]))}
    if len(previous) > 1:
        raise MemoryCaptureError("MEMORY_CLARIFICATION_REQUIRED", 409)
    if previous:
        row = previous[0]
        result = await memory_service.correct_memory(
            owner_id,
            row.id,
            row.version,
            capture.text,
            key,
            source_turn_id=source_turn_id or proposed_turn,
            conversation_id=conversation_id,
        )
        return {"status": "corrected", "memory": asdict(result)}
    result = await memory_service.create_memory(
        owner_id, conversation_id, source_turn_id or proposed_turn, capture.text, key
    )
    return {"status": "saved", "memory": asdict(result)}


__all__ = [
    "MemoryCaptureError",
    "confirm_memory",
    "propose_memory",
]
