"""Consent-aware memory capture shell around the existing memory service."""

import asyncio
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.context_operations import sign_proposal, verify_proposal
from app.domain.memory_capture import MemoryCapture, parse_capture
from app.infrastructure.database import Memory, now
from app.services.memory import MemoryService, memory_result

CAPTURE_KIND = "memory_capture"
PROPOSAL_TTL_SECONDS = 600
LEGACY_SUBJECT_SCAN = 500
LEGACY_SUBJECT_SCAN_MAX = 5000
TERMINAL_OUTCOMES = frozenset({"saved", "corrected", "retracted", "discarded"})
_SUBJECT_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _subject_lock(owner_id: str, subject: str | None) -> asyncio.Lock:
    key = (owner_id, subject or "explicit")
    lock = _SUBJECT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SUBJECT_LOCKS[key] = lock
    return lock


class MemoryCaptureError(Exception):
    def __init__(self, code: str, status: int = 409, **details):
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = details


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
    confirmation_id = str(uuid4())
    token = sign_proposal(
        {
            "v": 1,
            "kind": CAPTURE_KIND,
            "confirmation_id": confirmation_id,
            "revision": 1,
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
    return {
        "token": token,
        "text": capture.text,
        "explicit": capture.explicit,
        "confirmation_id": confirmation_id,
        "confirmation_revision": 1,
        "question": _confirmation_question(capture.text),
    }


def _confirmation_question(content: str) -> str:
    return f"Should I remember this: {content}?"


def _replacement_question(old: str, new: str) -> str:
    return (
        f'You already have "{old}". Should I replace it with "{new}"? '
        'Say "replace it" to update, or "keep it" to keep the old fact.'
    )


def _is_clarification(payload: dict) -> bool:
    """A clarification revision is anything beyond the first proposal."""
    return int(payload.get("revision", 1)) > 1 or payload.get("target_id") is not None


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
    if not isinstance(payload.get("confirmation_id"), str) or not isinstance(payload.get("revision"), int):
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    return (
        MemoryCapture(
            text=content, subject=subject, value=value, explicit=explicit, replaces=replaces, negated=negated
        ),
        payload,
    )


def _clarification_token(payload: dict, secret: str, *, target: Memory) -> tuple[str, int]:
    revision = int(payload["revision"]) + 1
    revised = {
        **payload,
        "revision": revision,
        "target_id": target.id,
        "target_version": target.version,
    }
    return sign_proposal(revised, secret), revision


async def _decision(db: AsyncSession, confirmation_id: str, revision: int):
    from app.infrastructure.database import MemoryCaptureDecision

    return await db.get(MemoryCaptureDecision, (confirmation_id, revision))


def _decision_values(
    *,
    payload: dict,
    owner_id: str,
    conversation_id: str,
    token: str,
    decision: str,
    outcome: str,
    result: dict | None = None,
    target: Memory | None = None,
) -> dict:
    return {
        "confirmation_id": payload["confirmation_id"],
        "revision": int(payload["revision"]),
        "owner_id": owner_id,
        "conversation_id": conversation_id,
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "decision": decision,
        "outcome": outcome,
        "target_id": target.id if target is not None else payload.get("target_id"),
        "target_version": target.version if target is not None else payload.get("target_version"),
        "result": result,
        "created_at": now(),
        "expires_at": datetime.fromtimestamp(int(payload["exp"]), UTC).isoformat(),
    }


def _replay_response(row, decision: str, payload: dict) -> dict:
    """Return a stored terminal response only for the same decision/target."""
    target_matches = decision != "replace" or row.target_id == payload.get("target_id")
    if row.decision != decision or not target_matches:
        raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)
    response = dict(row.result or {"status": row.outcome})
    response["idempotent"] = True
    return response


async def _claim_decision(
    db: AsyncSession,
    *,
    payload: dict,
    owner_id: str,
    conversation_id: str,
    token: str,
    decision: str,
    target: Memory | None = None,
):
    """Durably claim one proposal revision for one decision.

    The conditional write is committed before any embedding or memory effect so
    a concurrent request cannot apply a different decision to the same revision.
    Returns an existing terminal row for idempotent replay, otherwise ``None``.

    Limitation: a process crash after this commit but before the effect leaves
    the revision ``applying``. Retries then fail closed with
    ``MEMORY_DECISION_IN_PROGRESS`` rather than risk a duplicate write; an
    operator must reconcile that revision.
    """
    from app.infrastructure.database import MemoryCaptureDecision

    confirmation_id = payload["confirmation_id"]
    revision = int(payload["revision"])
    for _ in range(3):
        row = await _decision(db, confirmation_id, revision)
        if row is None:
            if decision not in {"save", "discard"}:
                # A replacement needs an existing awaiting_confirmation row;
                # the caller handles recovery or refuses without a claim.
                return None
            values = _decision_values(
                payload=payload,
                owner_id=owner_id,
                conversation_id=conversation_id,
                token=token,
                decision=decision,
                outcome="applying",
                target=target,
            )
            try:
                db.add(MemoryCaptureDecision(**values))
                await db.commit()
                return None
            except IntegrityError:
                # Another request inserted first; re-read and honour its claim.
                await db.rollback()
                continue
        if row.outcome == "applying":
            raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision=row.decision)
        if row.outcome in TERMINAL_OUTCOMES:
            return row
        if row.outcome != "awaiting_confirmation":
            raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)
        if decision not in {"replace", "discard"}:
            raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)
        values = _decision_values(
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            decision=decision,
            outcome="applying",
            target=target,
        )
        changed = await db.execute(
            update(MemoryCaptureDecision)
            .where(
                MemoryCaptureDecision.confirmation_id == confirmation_id,
                MemoryCaptureDecision.revision == revision,
                MemoryCaptureDecision.outcome == "awaiting_confirmation",
            )
            .values(**values)
        )
        if changed.rowcount == 1:
            await db.commit()
            return None
        await db.rollback()
    raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision=decision)


async def _release_claim(
    db: AsyncSession,
    *,
    payload: dict,
    decision: str,
    awaiting: bool = False,
) -> None:
    """Undo an uncommitted claim after a synchronous pre-effect failure.

    Crashes intentionally leave the claim ``applying``; this helper is only for
    failures the process observed and can safely retry.
    """
    from app.infrastructure.database import MemoryCaptureDecision

    query = delete(MemoryCaptureDecision).where(
        MemoryCaptureDecision.confirmation_id == payload["confirmation_id"],
        MemoryCaptureDecision.revision == int(payload["revision"]),
        MemoryCaptureDecision.outcome == "applying",
        MemoryCaptureDecision.decision == decision,
    )
    if awaiting:
        await db.execute(
            update(MemoryCaptureDecision)
            .where(
                MemoryCaptureDecision.confirmation_id == payload["confirmation_id"],
                MemoryCaptureDecision.revision == int(payload["revision"]),
                MemoryCaptureDecision.outcome == "applying",
                MemoryCaptureDecision.decision == decision,
            )
            .values(outcome="awaiting_confirmation", decision="clarify", result=None)
        )
    else:
        await db.execute(query)
    await db.commit()


def _apply_decision_row(row, *, values: dict, decision: str, result: dict | None) -> dict | None:
    """Update an existing claimed row, or replay a terminal receipt."""
    if row.outcome == "applying":
        if row.decision != decision:
            raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)
        for key, value in values.items():
            setattr(row, key, value)
        return None
    if row.outcome in TERMINAL_OUTCOMES and row.decision == decision:
        return result or dict(row.result or {"status": row.outcome})
    raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)


async def _record_decision(
    db: AsyncSession,
    *,
    payload: dict,
    owner_id: str,
    conversation_id: str,
    token: str,
    decision: str,
    outcome: str,
    result: dict | None = None,
    target: Memory | None = None,
    commit: bool = True,
    attempt: int = 0,
) -> dict:
    """Record an outcome without ever overwriting a terminal receipt."""
    from app.infrastructure.database import MemoryCaptureDecision

    row = await _decision(db, payload["confirmation_id"], int(payload["revision"]))
    values = _decision_values(
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision=decision,
        outcome=outcome,
        result=result,
        target=target,
    )
    if row is None:
        db.add(MemoryCaptureDecision(**values))
        if commit:
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if attempt >= 2:
                    raise
                return await _record_decision(
                    db,
                    payload=payload,
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    token=token,
                    decision=decision,
                    outcome=outcome,
                    result=result,
                    target=target,
                    commit=commit,
                    attempt=attempt + 1,
                )
        return result or {"status": outcome}
    replayed = _apply_decision_row(row, values=values, decision=decision, result=result)
    if replayed is not None:
        return replayed
    if commit:
        await db.commit()
    return result or {"status": outcome}


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
            .where(
                Memory.owner_id == owner_id,
                Memory.status == "active",
                Memory.subject == capture.subject,
            )
            .order_by(Memory.updated_at.desc(), Memory.id)
        )
    ).all()
    # Legacy rows created before the subject column are NULL. Page them
    # deterministically so an old contradiction cannot hide behind newer
    # unrelated rows; if the hard cap is reached without exhaustion, fail
    # closed instead of silently saving a contradictory fact.
    legacy: list[Memory] = []
    last_id = ""
    while True:
        page = (
            await db.scalars(
                select(Memory)
                .where(
                    Memory.owner_id == owner_id,
                    Memory.status == "active",
                    Memory.subject.is_(None),
                    Memory.id > last_id,
                )
                .order_by(Memory.id)
                .limit(LEGACY_SUBJECT_SCAN)
            )
        ).all()
        legacy.extend(page)
        if len(page) < LEGACY_SUBJECT_SCAN:
            break
        last_id = page[-1].id
        if len(legacy) >= LEGACY_SUBJECT_SCAN_MAX:
            raise MemoryCaptureError("MEMORY_CONTRADICTION_SCAN_INCOMPLETE", 409)
    matches: list[Memory] = []
    for row in [*rows, *legacy]:
        existing = parse_capture(row.content)
        if existing is not None and existing.subject == capture.subject:
            matches.append(row)
    return matches


async def _discard(
    db: AsyncSession,
    *,
    payload: dict,
    owner_id: str,
    conversation_id: str,
    token: str,
) -> dict:
    response = {"status": "discarded", "memory": None}
    # Claiming the revision before recording success means a losing discard can
    # never report ``discarded`` when a concurrent save/replace owns the effect.
    claimed = await _claim_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="discard",
    )
    if claimed is not None:
        return _replay_response(claimed, "discard", payload)
    await _record_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="discard",
        outcome="discarded",
        result=response,
    )
    return response


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
    """Apply a signed proposal revision only after explicit consent.

    Consent is bound to ``(confirmation_id, revision)``. A clarification mints
    a new revision token with an exact target id/version; the previous revision
    can never authorize a replacement.
    """
    if not secret:
        raise MemoryCaptureError("MEMORY_CAPTURE_NOT_CONFIGURED", 503)
    capture, payload = _verified(token, secret, owner_id, conversation_id)
    proposed_turn = payload.get("source_turn_id")
    if not isinstance(proposed_turn, str) or not proposed_turn:
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    confirmation_id = payload["confirmation_id"]
    revision = int(payload["revision"])

    existing = await _decision(db, confirmation_id, revision)
    if existing is not None:
        if existing.outcome == "applying":
            # A claim is not a receipt: never report success for an effect whose
            # outcome this process cannot observe (for example after a crash).
            raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision=existing.decision)
        if existing.outcome != "awaiting_confirmation":
            return _replay_response(existing, decision, payload)

    if decision == "discard":
        return await _discard(
            db,
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
        )
    if decision not in {"save", "replace"}:
        raise MemoryCaptureError("INVALID_DECISION", 422)

    if decision == "replace":
        return await _confirm_replace(
            db,
            memory_service,
            existing=existing,
            owner_id=owner_id,
            conversation_id=conversation_id,
            proposed_turn=proposed_turn,
            source_turn_id=source_turn_id,
            capture=capture,
            payload=payload,
            token=token,
            revision=revision,
        )

    if _is_clarification(payload):
        # A clarification revision is consent for one exact replacement, never
        # for a fresh save. Without a committed effect this must fail closed so
        # a lost decision row cannot reopen a new write through ``save``.
        target_id = payload.get("target_id")
        target_version = payload.get("target_version")
        if not isinstance(target_id, str) or not isinstance(target_version, int):
            raise MemoryCaptureError("PROPOSAL_INVALID", 400)
        return await _recover_clarification(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            capture=capture,
            payload=payload,
            token=token,
            decision=decision,
            target_id=target_id,
            target_version=target_version,
        )

    if existing is not None:
        # A clarification token must never be replayed as a plain save.
        raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=existing.outcome)

    return await _apply_consent(
        db,
        memory_service,
        owner_id=owner_id,
        conversation_id=conversation_id,
        proposed_turn=proposed_turn,
        source_turn_id=source_turn_id,
        capture=capture,
        payload=payload,
        token=token,
        secret=secret,
    )


async def _recover_clarification(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    payload: dict,
    token: str,
    decision: str,
    target_id: str,
    target_version: int,
) -> dict:
    """Report a committed clarification effect, or refuse to write anew."""
    applied = await _exact_content(db, owner_id=owner_id, capture=capture, status="active")
    if applied is not None and applied.supersedes_id == target_id:
        response = {"status": "corrected", "memory": asdict(memory_result(applied))}
        await _record_decision(
            db,
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            decision=decision,
            outcome=response["status"],
            result=response,
        )
        return response
    if capture.negated:
        previous = await db.scalar(select(Memory).where(Memory.id == target_id, Memory.owner_id == owner_id))
        if previous is not None and previous.status == "superseded" and previous.version == target_version + 1:
            response = {"status": "retracted", "memory": None}
            await _record_decision(
                db,
                payload=payload,
                owner_id=owner_id,
                conversation_id=conversation_id,
                token=token,
                decision=decision,
                outcome=response["status"],
                result=response,
            )
            return response
    raise MemoryCaptureError("MEMORY_FRESH_CONFIRMATION_REQUIRED", 409, target_id=target_id)


async def _confirm_replace(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    existing,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    source_turn_id: str | None,
    capture: MemoryCapture,
    payload: dict,
    token: str,
    revision: int,
) -> dict:
    target_id = payload.get("target_id")
    target_version = payload.get("target_version")
    if not isinstance(target_id, str) or not isinstance(target_version, int):
        raise MemoryCaptureError("CONFIRMATION_STALE", 409, revision=revision)
    if existing is not None:
        if existing.outcome != "awaiting_confirmation":
            raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=existing.outcome)
        if existing.target_id != target_id or existing.target_version != target_version:
            raise MemoryCaptureError("CONFIRMATION_STALE", 409, revision=revision)
    return await _replace_target(
        db,
        memory_service,
        owner_id=owner_id,
        conversation_id=conversation_id,
        proposed_turn=proposed_turn,
        source_turn_id=source_turn_id,
        capture=capture,
        payload=payload,
        token=token,
        target_id=target_id,
        target_version=target_version,
        recovery_only=existing is None,
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
    payload: dict,
    token: str,
    secret: str,
) -> dict:
    async with _subject_lock(owner_id, capture.subject):
        key = f"memcap:{owner_id}:{capture.subject or capture.text}"
        advisory = db.bind is not None and db.bind.dialect.name == "postgresql"
        if advisory:
            # Session-level (not transaction-level) so the lock survives the
            # durable decision-claim commit before the memory effect.
            await db.execute(text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": key})
        try:
            return await _write_consented(
                db,
                memory_service,
                owner_id=owner_id,
                conversation_id=conversation_id,
                proposed_turn=proposed_turn,
                source_turn_id=source_turn_id,
                capture=capture,
                payload=payload,
                token=token,
                secret=secret,
            )
        finally:
            if advisory:
                if db.in_transaction():
                    await db.rollback()
                await db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
                await db.commit()


def _conflict_rows(rows: list[Memory], capture: MemoryCapture) -> list[Memory]:
    return [
        row
        for row in rows
        if (parsed := parse_capture(row.content)) is not None
        and (parsed.value != capture.value or parsed.negated != capture.negated)
    ]


async def _clarify(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    payload: dict,
    token: str,
    secret: str,
    target: Memory,
) -> None:
    clarified, revision = _clarification_token(payload, secret, target=target)
    revised = {**payload, "revision": revision, "target_id": target.id, "target_version": target.version}
    prior = await _decision(db, payload["confirmation_id"], revision)
    if prior is None:
        await _record_decision(
            db,
            payload=revised,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=clarified,
            decision="clarify",
            outcome="awaiting_confirmation",
            target=target,
        )
    elif prior.outcome != "awaiting_confirmation":
        raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=prior.outcome)
    raise MemoryCaptureError(
        "MEMORY_CLARIFICATION_REQUIRED",
        409,
        token=clarified,
        confirmation_id=payload["confirmation_id"],
        confirmation_revision=revision,
        target_id=target.id,
        target_version=target.version,
        question=_replacement_question(target.content, capture.text),
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
    payload: dict,
    token: str,
    secret: str,
) -> dict:
    key = _request_key(owner_id, proposed_turn, capture.text)
    active_same = await _exact_content(db, owner_id=owner_id, capture=capture, status="active")
    if active_same is not None:
        response = {"status": "already_saved", "memory": asdict(memory_result(active_same))}
        await _record_decision(
            db,
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            decision="save",
            outcome="saved",
            result=response,
        )
        return response
    replay = await _exact_content(db, owner_id=owner_id, capture=capture, source_turn_id=proposed_turn)
    if replay is not None:
        response = {"status": "already_saved", "memory": asdict(memory_result(replay))}
        await _record_decision(
            db,
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            decision="save",
            outcome="saved",
            result=response,
        )
        return response
    previous = await _contradictions(db, owner_id=owner_id, capture=capture)
    conflicting = _conflict_rows(previous, capture)
    if len(conflicting) > 1:
        # Never pick one contradictory active row silently.
        raise MemoryCaptureError(
            "MEMORY_MULTIPLE_TARGETS",
            409,
            targets=[{"id": row.id, "version": row.version} for row in conflicting],
        )
    if conflicting:
        await _clarify(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            capture=capture,
            payload=payload,
            token=token,
            secret=secret,
            target=conflicting[0],
        )
    claimed = await _claim_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="save",
    )
    if claimed is not None:
        return _replay_response(claimed, "save", payload)
    try:
        result = await memory_service.create_memory(
            owner_id, conversation_id, source_turn_id or proposed_turn, capture.text, key
        )
    except Exception:
        # No memory effect was observed; release the claim so a retry can
        # proceed. A crash (not an observed failure) leaves it ``applying``.
        await _release_claim(db, payload=payload, decision="save")
        raise
    response = {"status": "saved", "memory": asdict(result)}
    await _record_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="save",
        outcome="saved",
        result=response,
    )
    return response


async def _replace_target(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    source_turn_id: str | None,
    capture: MemoryCapture,
    payload: dict,
    token: str,
    target_id: str,
    target_version: int,
    recovery_only: bool = False,
) -> dict:
    target = await db.scalar(
        select(Memory).where(Memory.id == target_id, Memory.owner_id == owner_id, Memory.status == "active")
    )
    # Durable recovery before target validation: the effect may have committed
    # while its decision receipt did not. A matching corrected row is the
    # successful outcome for a replacement.
    applied = await _exact_content(db, owner_id=owner_id, capture=capture, status="active")
    if applied is not None and applied.supersedes_id == target_id:
        response = {"status": "corrected", "memory": asdict(memory_result(applied))}
        await _record_decision(
            db,
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            decision="replace",
            outcome=response["status"],
            result=response,
        )
        return response
    # A retraction has no new row; its receipt is the superseded target version.
    if capture.negated:
        previous = await db.scalar(select(Memory).where(Memory.id == target_id, Memory.owner_id == owner_id))
        if previous is not None and previous.status == "superseded" and previous.version == target_version + 1:
            response = {"status": "retracted", "memory": None}
            await _record_decision(
                db,
                payload=payload,
                owner_id=owner_id,
                conversation_id=conversation_id,
                token=token,
                decision="replace",
                outcome=response["status"],
                result=response,
            )
            return response
    if recovery_only:
        # Without an awaiting confirmation row, only a previously committed
        # effect may be reported. A missing row never authorizes a new write.
        raise MemoryCaptureError("MEMORY_FRESH_CONFIRMATION_REQUIRED", 409, target_id=target_id)
    if target is None or target.version != target_version:
        raise MemoryCaptureError("TARGET_CHANGED", 409, target_id=target_id)
    # Validate before claiming so an already-stale clarification is not turned
    # into an in-progress claim; the correction itself still re-checks version.
    claimed = await _claim_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="replace",
        target=target,
    )
    if claimed is not None:
        return _replay_response(claimed, "replace", payload)
    try:
        if capture.negated:
            changed = await db.execute(
                update(Memory)
                .where(
                    Memory.id == target_id,
                    Memory.owner_id == owner_id,
                    Memory.status == "active",
                    Memory.version == target_version,
                )
                .values(status="superseded", version=Memory.version + 1, updated_at=now())
            )
            if changed.rowcount != 1:
                raise MemoryCaptureError("TARGET_CHANGED", 409, target_id=target_id)
            response = {"status": "retracted", "memory": None}
            await _record_decision(
                db,
                payload=payload,
                owner_id=owner_id,
                conversation_id=conversation_id,
                token=token,
                decision="replace",
                outcome=response["status"],
                result=response,
                target=target,
                commit=False,
            )
            await db.commit()
            return response
        key = _request_key(owner_id, f"{proposed_turn}:replace:{payload['revision']}", capture.text)
        try:
            result = await memory_service.correct_memory(
                owner_id,
                target_id,
                target_version,
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
                raise MemoryCaptureError("TARGET_CHANGED", 409, target_id=target_id) from exc
            raise
        response = {"status": "corrected", "memory": asdict(result)}
        await _record_decision(
            db,
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            decision="replace",
            outcome=response["status"],
            result=response,
            target=target,
        )
        return response
    except Exception:
        # The observed failure happened before a memory effect; restore the
        # clarification so a retry can decide again. A crashed process leaves
        # the claim ``applying`` and retries fail closed with 409.
        await _release_claim(db, payload=payload, decision="replace", awaiting=True)
        raise


__all__ = [
    "MemoryCaptureError",
    "confirm_memory",
    "propose_memory",
]
