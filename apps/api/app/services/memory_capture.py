"""Consent-aware memory capture shell around the existing memory service."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.domain.context_operations import sign_proposal, verify_proposal
from app.domain.contracts import SCOPE_CONVERSATION, SCOPE_GLOBAL, MemoryMode
from app.domain.memory_capture import (
    MemoryCapture,
    is_referential,
    parse_capture,
    parse_strict_capture,
    resolve_subject,
)
from app.domain.memory_policy import Decision, plan_for_proposal
from app.infrastructure.database import Memory, MemoryWriteRequest, now
from app.infrastructure.upsert import insert_ignore
from app.services.authorization import owned_conversation
from app.services.leases import lease_expired, lease_expiry
from app.services.memory import MemoryService, memory_result
from app.services.memory_erasure_lock import lock_owner_memories
from app.services.memory_scope import visible_in
from app.services.memory_visibility import delete_memory
from app.services.temporary_memory import PromotionConflict, promote_temporary_memory

CAPTURE_KIND = "memory_capture"
PROPOSAL_TTL_SECONDS = 600
LEGACY_SUBJECT_SCAN = 500
LEGACY_SUBJECT_SCAN_MAX = 5000
TERMINAL_OUTCOMES = frozenset({"saved", "corrected", "retracted", "discarded"})
_SUBJECT_LOCKS: dict[tuple[object, str, str], asyncio.Lock] = {}


def _subject_lock(owner_id: str, subject: str | None) -> asyncio.Lock:
    # Keyed by loop: the cache is process-wide, but an asyncio lock belongs to
    # the loop that first awaited it (tests run several loops per process).
    key = (asyncio.get_running_loop(), owner_id, subject or "explicit")
    lock = _SUBJECT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SUBJECT_LOCKS[key] = lock
    return lock


@asynccontextmanager
async def _advisory_subject_lock(db: AsyncSession, owner_id: str, subject: str | None):
    """Serialize one owner/subject across workers.

    The PostgreSQL lock is ``pg_advisory_xact_lock``: it is owned by a dedicated
    transaction that brackets the protected write and is released by the commit
    (or rollback) that ends it. The connection stays checked out for that whole
    span, so it can never return to the pool holding the lock, and a crash
    rolls the transaction back instead of leaking a session-level lock.
    """
    async with _subject_lock(owner_id, subject):
        bind = db.bind
        if bind is None or bind.dialect.name != "postgresql":
            yield
            return
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        key = f"memcap:{owner_id}:{subject or 'explicit'}"
        async with async_sessionmaker(engine, expire_on_commit=False)() as lock_db, lock_db.begin():
            await lock_db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
            yield


@dataclass(frozen=True)
class _Claim:
    """One owned lease, or a terminal row that may be replayed."""

    lease_id: str | None = None
    replay: object | None = None


class MemoryCaptureError(Exception):
    def __init__(self, code: str, status: int = 409, **details):
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = details


def _request_key(owner_id: str, source_turn_id: str, content: str) -> str:
    digest = hashlib.sha256(f"{owner_id}|{source_turn_id}|{content.casefold()}".encode()).hexdigest()
    return f"memcap:{digest[:80]}"


def propose_memory(
    text: str, *, owner_id: str, conversation_id: str, source_turn_id: str, secret: str, strict: bool = False
) -> dict | None:
    """Return a signed, read-only proposal or None when the text is not a fact.

    ``strict`` (used when the model classifier is available) interprets only the
    exact "remember that <clause>" shape; the model handles everything else.
    """
    capture = parse_strict_capture(text) if strict else parse_capture(text)
    if capture is None:
        return None
    return proposal_for_capture(
        capture, owner_id=owner_id, conversation_id=conversation_id, source_turn_id=source_turn_id, secret=secret
    )


def proposal_for_capture(
    capture: MemoryCapture, *, owner_id: str, conversation_id: str, source_turn_id: str, secret: str
) -> dict:
    """Sign an already validated candidate; shared by the regex path and the typed tool."""
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
        "negated": capture.negated,
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
            text=content,
            # A signed token from before subjects existed may carry None; resolve
            # it so a new row can never be written into the legacy NULL scan.
            subject=subject or resolve_subject(content),
            value=value,
            explicit=explicit,
            replaces=replaces,
            negated=negated,
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


async def record_proposal(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    proposal: dict | None,
) -> None:
    """Persist the proposal identity; the unique key makes retries idempotent."""
    if proposal is None:
        return
    confirmation_id = proposal["confirmation_id"]
    await insert_ignore(
        db,
        MemoryWriteRequest,
        {
            "id": str(uuid4()),
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "request_key": confirmation_id,
            "fingerprint": hashlib.sha256(proposal["token"].encode()).hexdigest(),
            "state": "proposed",
            "result": {"proposal": proposal},
            "created_at": now(),
            "updated_at": now(),
        },
        index_elements=["owner_id", "request_key"],
    )


async def load_proposal(db: AsyncSession, *, owner_id: str, confirmation_id: str):
    """Return ``(proposal, state)`` for a recorded proposal, or ``(None, None)``."""
    row = await db.scalar(
        select(MemoryWriteRequest).where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.request_key == confirmation_id,
        )
    )
    if row is None:
        return None, None
    proposal = (row.result or {}).get("proposal")
    return (proposal if isinstance(proposal, dict) else None), row.state


def _stored_proposal(row: MemoryWriteRequest | None) -> tuple[dict | None, str | None]:
    """The stored proposal and its signed token, unless the row was scrubbed."""
    if row is None or not isinstance(row.result, dict) or row.result.get("scrubbed"):
        return None, None
    proposal = row.result.get("proposal")
    if not isinstance(proposal, dict):
        return None, None
    token = proposal.get("token")
    return proposal, token if isinstance(token, str) else None


async def pending_proposal(db: AsyncSession, *, owner_id: str, conversation_id: str, secret: str) -> dict | None:
    """The newest unexpired armed proposal for this conversation, as its owner sees it.

    Reads only the owner's rows for this conversation. The fact comes from the
    stored proposal snapshot, so it does not need the worker; the signed token
    supplies the authoritative expiry.
    """
    if not secret:
        raise MemoryCaptureError("MEMORY_CAPTURE_NOT_CONFIGURED", 503)
    rows = (
        await db.scalars(
            select(MemoryWriteRequest)
            .where(
                MemoryWriteRequest.owner_id == owner_id,
                MemoryWriteRequest.conversation_id == conversation_id,
                MemoryWriteRequest.state == "proposed",
            )
            .order_by(MemoryWriteRequest.created_at.desc(), MemoryWriteRequest.id.desc())
            .limit(20)
        )
    ).all()
    for row in rows:
        proposal, token = _stored_proposal(row)
        if proposal is None or token is None:
            continue
        confirmation_id = proposal.get("confirmation_id")
        fact = proposal.get("text")
        if not isinstance(confirmation_id, str) or not isinstance(fact, str):
            continue
        try:
            capture, payload = _verified(token, secret, owner_id, conversation_id)
        except MemoryCaptureError:
            continue
        replaces = None
        try:
            conflicts = _conflict_rows(
                await _indexed_contradictions(db, owner_id=owner_id, conversation_id=conversation_id, capture=capture),
                capture,
            )
        except MemoryCaptureError:
            conflicts = []
        if len(conflicts) == 1:
            replaces = {"id": conflicts[0].id, "version": conflicts[0].version, "content": conflicts[0].content}
        return {
            "id": confirmation_id,
            "fact": fact,
            "expires_at": datetime.fromtimestamp(int(payload["exp"]), UTC).isoformat(),
            "replaces": replaces,
        }
    return None


async def _replayed_decision(db: AsyncSession, *, owner_id: str, confirmation_id: str, decision: str) -> dict | None:
    """The stored outcome of a terminal proposal revision, or ``None`` while it is still pending.

    A ``replace`` request may have completed as a plain save (no visible
    conflict), so replay accepts either stored decision before refusing.
    """
    candidates = ("replace", "save") if decision == "replace" else (decision,)
    last_error: MemoryCaptureError | None = None
    for candidate in candidates:
        try:
            replayed = await _resolved_proposal_response(
                db, owner_id=owner_id, confirmation_id=confirmation_id, decision=candidate
            )
        except MemoryCaptureError as exc:
            last_error = exc
            continue
        if replayed is not None:
            return replayed
    if last_error is not None:
        raise last_error
    return None


async def _require_replace_target(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    target_id: str,
    target_version: int,
) -> Memory:
    """The requested target must still be the single visible conflict the user was shown."""
    if (
        await _exact_content(db, owner_id=owner_id, conversation_id=conversation_id, capture=capture, status="active")
        is not None
    ):
        raise MemoryCaptureError("MEMORY_PROPOSAL_STALE", 409)
    conflicts = _conflict_rows(
        await _contradictions(db, owner_id=owner_id, conversation_id=conversation_id, capture=capture), capture
    )
    if len(conflicts) > 1:
        raise MemoryCaptureError(
            "MEMORY_MULTIPLE_TARGETS",
            409,
            targets=[{"id": row.id, "version": row.version} for row in conflicts],
        )
    if len(conflicts) != 1 or conflicts[0].id != target_id or conflicts[0].version != target_version:
        raise MemoryCaptureError(
            "MEMORY_PROPOSAL_TARGET_CHANGED", 409, target_id=target_id, target_version=target_version
        )
    return conflicts[0]


async def _confirm_replace_decision(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    payload: dict,
    secret: str,
    target: Memory,
) -> dict:
    """Arm the clarification token, then let the replace path be the only writer."""
    token, _ = await _arm_clarification(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        capture=capture,
        payload=payload,
        secret=secret,
        target=target,
    )
    try:
        result = await confirm_memory(
            db,
            memory_service,
            token=token,
            decision="replace",
            owner_id=owner_id,
            conversation_id=conversation_id,
            source_turn_id=None,
            secret=secret,
        )
    except MemoryCaptureError as error:
        if error.code == "TARGET_CHANGED":
            raise MemoryCaptureError(
                "MEMORY_PROPOSAL_TARGET_CHANGED",
                409,
                target_id=target.id,
                target_version=target.version,
            ) from error
        raise
    if result.get("status") == "already_saved":
        raise MemoryCaptureError("MEMORY_PROPOSAL_STALE", 409)
    return result


async def _resolve_replace_proposal(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    proposal_id: str,
    token: str,
    secret: str,
    target_id: str,
    target_version: int,
) -> dict:
    replayed = await _replayed_decision(db, owner_id=owner_id, confirmation_id=proposal_id, decision="replace")
    if replayed is not None:
        return replayed
    capture, payload = _verified(token, secret, owner_id, conversation_id)
    target = await _require_replace_target(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        capture=capture,
        target_id=target_id,
        target_version=target_version,
    )
    return await _confirm_replace_decision(
        db,
        memory_service,
        owner_id=owner_id,
        conversation_id=conversation_id,
        capture=capture,
        payload=payload,
        secret=secret,
        target=target,
    )


async def resolve_proposal(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    proposal_id: str,
    decision: str,
    secret: str,
    mode: MemoryMode,
    target_id: str | None = None,
    target_version: int | None = None,
) -> dict:
    """Resolve one stored proposal by id with the same rules as the spoken yes/no."""
    if not secret:
        raise MemoryCaptureError("MEMORY_CAPTURE_NOT_CONFIGURED", 503)
    if decision not in {"save", "decline", "replace"}:
        raise MemoryCaptureError("INVALID_DECISION", 422)
    if decision == "replace" and (not target_id or target_version is None):
        raise MemoryCaptureError("INVALID_DECISION", 422)
    row = await db.scalar(
        select(MemoryWriteRequest).where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.conversation_id == conversation_id,
            MemoryWriteRequest.request_key == proposal_id,
        )
    )
    proposal, token = _stored_proposal(row)
    if proposal is None or token is None:
        raise MemoryCaptureError("MEMORY_PROPOSAL_NOT_FOUND", 404)
    if decision == "decline":
        return await confirm_memory(
            db,
            memory_service,
            token=token,
            decision="discard",
            owner_id=owner_id,
            conversation_id=conversation_id,
            source_turn_id=None,
            secret=secret,
        )
    # A proposal armed before the user turned memory off in this chat must not be savable afterwards.
    if mode == "off":
        raise MemoryCaptureError("MEMORY_OFF", 409)
    _, fact, explicit, negated = proposal_summary(token, secret, owner_id, conversation_id)
    if plan_for_proposal(mode, fact, explicit=explicit, negated=negated) is Decision.SKIP:
        raise MemoryCaptureError("MEMORY_SENSITIVE", 409)
    if decision == "replace":
        return await _resolve_replace_proposal(
            db,
            memory_service,
            owner_id=owner_id,
            conversation_id=conversation_id,
            proposal_id=proposal_id,
            token=token,
            secret=secret,
            target_id=target_id,
            target_version=target_version,
        )
    return await confirm_memory(
        db,
        memory_service,
        token=token,
        decision="save",
        owner_id=owner_id,
        conversation_id=conversation_id,
        source_turn_id=None,
        secret=secret,
    )


async def _ensure_proposal(db: AsyncSession, *, owner_id: str, conversation_id: str, payload: dict, token: str) -> None:
    proposal = {
        "token": token,
        "text": payload["content"],
        "confirmation_id": payload["confirmation_id"],
        "confirmation_revision": int(payload["revision"]),
        "question": _confirmation_question(payload["content"]),
    }
    await record_proposal(db, owner_id=owner_id, conversation_id=conversation_id, proposal=proposal)


async def _claim_proposal(db: AsyncSession, *, owner_id: str, confirmation_id: str, decision: str) -> bool:
    """Atomically claim a proposal; terminal rows are never claimable.

    A row already claimed by the same decision may be retried. A different
    decision may take over only after the claim goes stale.
    """
    row = await db.scalar(
        select(MemoryWriteRequest).where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.request_key == confirmation_id,
        )
    )
    if row is None:
        return False
    if row.state not in {"proposed", "claimed"}:
        return False
    result = dict(row.result or {})
    if row.state == "claimed" and result.get("claimed_decision") != decision:
        claimed_at = result.get("claimed_at")
        if not isinstance(claimed_at, str) or not _claim_stale(claimed_at):
            return False
    stale = row.updated_at
    merged = {**result, "claimed_decision": decision, "claimed_at": now()}
    claimed = await db.execute(
        update(MemoryWriteRequest)
        .where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.request_key == confirmation_id,
            MemoryWriteRequest.state == row.state,
            MemoryWriteRequest.updated_at == stale,
        )
        .values(state="claimed", updated_at=now(), result=merged)
    )
    await db.commit()
    return claimed.rowcount == 1


def _claim_stale(claimed_at: str) -> bool:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(claimed_at)).total_seconds() > 30
    except ValueError:
        return True


async def _finish_proposal(
    db: AsyncSession, *, owner_id: str, confirmation_id: str, state: str, decision: str, result: dict
) -> None:
    await db.execute(
        update(MemoryWriteRequest)
        .where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.request_key == confirmation_id,
            MemoryWriteRequest.state == "claimed",
        )
        .values(
            state=state,
            updated_at=now(),
            result={
                **(await _proposal_result(db, owner_id, confirmation_id)),
                "decision": decision,
                "response": result,
            },
        )
    )


async def _proposal_result(db: AsyncSession, owner_id: str, confirmation_id: str) -> dict:
    row = await db.scalar(
        select(MemoryWriteRequest).where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.request_key == confirmation_id,
        )
    )
    return dict(row.result or {}) if row is not None else {}


async def _release_proposal(db: AsyncSession, *, owner_id: str, confirmation_id: str) -> None:
    await db.execute(
        update(MemoryWriteRequest)
        .where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.request_key == confirmation_id,
            MemoryWriteRequest.state == "claimed",
        )
        .values(state="proposed", updated_at=now())
    )
    await db.commit()


async def _resolved_proposal_response(
    db: AsyncSession, *, owner_id: str, confirmation_id: str, decision: str
) -> dict | None:
    """Replay a matching terminal decision, otherwise refuse a resolved proposal."""
    _, state = await load_proposal(db, owner_id=owner_id, confirmation_id=confirmation_id)
    if state not in {"saved", "discarded"}:
        return None
    row = await db.scalar(
        select(MemoryWriteRequest).where(
            MemoryWriteRequest.owner_id == owner_id,
            MemoryWriteRequest.request_key == confirmation_id,
        )
    )
    stored = dict(row.result or {}) if row is not None else {}
    if stored.get("scrubbed"):
        # The memory behind the proposal was deleted, so the token can only report a discarded outcome.
        return {"status": "discarded", "memory": None, "idempotent": True}
    if stored.get("decision") == decision and isinstance(stored.get("response"), dict):
        return {**stored["response"], "idempotent": True}
    raise MemoryCaptureError("MEMORY_PROPOSAL_RESOLVED", 409, confirmation_id=confirmation_id)


async def _decision(db: AsyncSession, confirmation_id: str, revision: int, owner_id: str, *, lock: bool = False):
    """Read one decision revision, owner-scoped and refreshed from the database (optionally row-locked)."""
    from app.infrastructure.database import MemoryCaptureDecision

    query = (
        select(MemoryCaptureDecision)
        .where(
            MemoryCaptureDecision.confirmation_id == confirmation_id,
            MemoryCaptureDecision.revision == revision,
            MemoryCaptureDecision.owner_id == owner_id,
        )
        .execution_options(populate_existing=True)
    )
    return await db.scalar(query.with_for_update() if lock else query)


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
    lease_id: str | None = None,
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
        "lease_id": lease_id,
        "lease_expires_at": lease_expiry() if lease_id is not None else None,
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
) -> _Claim | None:
    """Durably claim one proposal revision for one decision under a fenced lease.

    The conditional write is committed before any embedding or memory effect so
    a concurrent request cannot apply a different decision to the same revision.
    A claim carries a lease id; every later effect and receipt must present it.
    Returns a terminal row for idempotent replay, or ``None`` when no claim is
    possible (a replacement without its awaiting row).

    An expired ``applying`` claim for the *same* decision may be taken over by a
    retry. The takeover replaces ``lease_id``, so the previous holder's fenced
    receipt write is invalidated instead of interleaving.
    """
    from app.infrastructure.database import MemoryCaptureDecision

    confirmation_id = payload["confirmation_id"]
    revision = int(payload["revision"])
    for _ in range(3):
        row = await _decision(db, confirmation_id, revision, owner_id)
        if row is None:
            if decision not in {"save", "discard"}:
                # A replacement needs an existing awaiting_confirmation row;
                # the caller handles recovery or refuses without a claim.
                return None
            lease_id = str(uuid4())
            values = _decision_values(
                payload=payload,
                owner_id=owner_id,
                conversation_id=conversation_id,
                token=token,
                decision=decision,
                outcome="applying",
                target=target,
                lease_id=lease_id,
            )
            try:
                db.add(MemoryCaptureDecision(**values))
                await db.commit()
                return _Claim(lease_id=lease_id)
            except IntegrityError:
                # Another request inserted first; re-read and honour its claim.
                await db.rollback()
                continue
        if row.outcome == "applying":
            if not lease_expired(row.lease_expires_at) or row.decision != decision:
                raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision=row.decision)
            lease_id = str(uuid4())
            values = _decision_values(
                payload=payload,
                owner_id=owner_id,
                conversation_id=conversation_id,
                token=token,
                decision=decision,
                outcome="applying",
                target=target,
                lease_id=lease_id,
            )
            changed = await db.execute(
                update(MemoryCaptureDecision)
                .where(
                    MemoryCaptureDecision.confirmation_id == confirmation_id,
                    MemoryCaptureDecision.revision == revision,
                    MemoryCaptureDecision.owner_id == owner_id,
                    MemoryCaptureDecision.outcome == "applying",
                    MemoryCaptureDecision.lease_id == row.lease_id,
                )
                .values(**values)
            )
            if changed.rowcount == 1:
                await db.commit()
                return _Claim(lease_id=lease_id)
            await db.rollback()
            continue
        if row.outcome in TERMINAL_OUTCOMES:
            return _Claim(replay=row)
        if row.outcome != "awaiting_confirmation":
            raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)
        if decision not in {"replace", "discard"}:
            raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)
        lease_id = str(uuid4())
        values = _decision_values(
            payload=payload,
            owner_id=owner_id,
            conversation_id=conversation_id,
            token=token,
            decision=decision,
            outcome="applying",
            target=target,
            lease_id=lease_id,
        )
        changed = await db.execute(
            update(MemoryCaptureDecision)
            .where(
                MemoryCaptureDecision.confirmation_id == confirmation_id,
                MemoryCaptureDecision.revision == revision,
                MemoryCaptureDecision.owner_id == owner_id,
                MemoryCaptureDecision.outcome == "awaiting_confirmation",
            )
            .values(**values)
        )
        if changed.rowcount == 1:
            await db.commit()
            return _Claim(lease_id=lease_id)
        await db.rollback()
    raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision=decision)


async def _require_lease(db: AsyncSession, payload: dict, lease_id: str | None, *, lock: bool = False) -> None:
    """Refuse an effect unless this worker still owns the live claim lease.

    ``lock=True`` takes the decision row lock so a concurrent delete (which discards the decision) serializes with
    the effect. Only the transaction that commits the effect may lock: the caller's own session stays open across
    the nested write transaction, and locking the same row from both would wait on itself.
    """
    row = await _decision(db, payload["confirmation_id"], int(payload["revision"]), payload["owner_id"], lock=lock)
    if (
        lease_id is None
        or row is None
        or row.outcome != "applying"
        or row.lease_id != lease_id
        or lease_expired(row.lease_expires_at)
    ):
        raise MemoryCaptureError("MEMORY_LEASE_LOST", 409, confirmation_id=payload["confirmation_id"])
    if lock:
        # A delete that ran before this transaction started has already committed its tombstone of the proposal.
        proposal = await db.scalar(
            select(MemoryWriteRequest).where(
                MemoryWriteRequest.owner_id == payload["owner_id"],
                MemoryWriteRequest.request_key == payload["confirmation_id"],
            )
        )
        if proposal is not None and (
            proposal.state == "discarded" or (isinstance(proposal.result, dict) and proposal.result.get("scrubbed"))
        ):
            raise MemoryCaptureError("MEMORY_LEASE_LOST", 409, confirmation_id=payload["confirmation_id"])


async def _release_claim(
    db: AsyncSession,
    *,
    payload: dict,
    decision: str,
    lease_id: str | None,
    awaiting: bool = False,
) -> None:
    """Undo an uncommitted claim after a synchronous pre-effect failure.

    The release is fenced by ``lease_id`` so a worker that lost its lease can
    never delete or restore a later holder's claim. Crashes intentionally leave
    the claim ``applying``; this helper is only for failures the process
    observed and can safely retry.
    """
    from app.infrastructure.database import MemoryCaptureDecision

    if awaiting:
        await db.execute(
            update(MemoryCaptureDecision)
            .where(
                MemoryCaptureDecision.confirmation_id == payload["confirmation_id"],
                MemoryCaptureDecision.revision == int(payload["revision"]),
                MemoryCaptureDecision.owner_id == payload["owner_id"],
                MemoryCaptureDecision.outcome == "applying",
                MemoryCaptureDecision.decision == decision,
                MemoryCaptureDecision.lease_id == lease_id,
            )
            .values(
                outcome="awaiting_confirmation", decision="clarify", result=None, lease_id=None, lease_expires_at=None
            )
        )
    else:
        await db.execute(
            delete(MemoryCaptureDecision).where(
                MemoryCaptureDecision.confirmation_id == payload["confirmation_id"],
                MemoryCaptureDecision.revision == int(payload["revision"]),
                MemoryCaptureDecision.owner_id == payload["owner_id"],
                MemoryCaptureDecision.outcome == "applying",
                MemoryCaptureDecision.decision == decision,
                MemoryCaptureDecision.lease_id == lease_id,
            )
        )
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


async def _fenced_receipt(
    db: AsyncSession,
    *,
    payload: dict,
    row,
    values: dict,
    decision: str,
    commit: bool,
    lease_id: str,
) -> None:
    """Turn an ``applying`` claim into a receipt only for its lease owner."""
    from app.infrastructure.database import MemoryCaptureDecision

    if row.decision != decision:
        raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=row.outcome)
    changed = await db.execute(
        update(MemoryCaptureDecision)
        .where(
            MemoryCaptureDecision.confirmation_id == payload["confirmation_id"],
            MemoryCaptureDecision.revision == int(payload["revision"]),
            MemoryCaptureDecision.owner_id == values["owner_id"],
            MemoryCaptureDecision.outcome == "applying",
            MemoryCaptureDecision.lease_id == lease_id,
        )
        .values(**values)
    )
    if changed.rowcount != 1:
        if commit:
            await db.rollback()
        raise MemoryCaptureError("MEMORY_LEASE_LOST", 409, confirmation_id=payload["confirmation_id"])
    if commit:
        await db.commit()


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
    lease_id: str | None = None,
    attempt: int = 0,
) -> dict:
    """Record an outcome without ever overwriting a terminal receipt.

    When ``lease_id`` is supplied the write is fenced: only the worker that
    still owns the claim may turn ``applying`` into a terminal receipt.
    """
    from app.infrastructure.database import MemoryCaptureDecision

    row = await _decision(db, payload["confirmation_id"], int(payload["revision"]), owner_id)
    values = _decision_values(
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision=decision,
        outcome=outcome,
        result=result,
        target=target,
        lease_id=lease_id,
    )
    if outcome in TERMINAL_OUTCOMES:
        values["lease_id"] = None
        values["lease_expires_at"] = None
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
                    lease_id=lease_id,
                    attempt=attempt + 1,
                )
        return result or {"status": outcome}
    if row.outcome == "applying" and lease_id is not None:
        await _fenced_receipt(
            db,
            payload=payload,
            row=row,
            values=values,
            decision=decision,
            commit=commit,
            lease_id=lease_id,
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
    conversation_id: str,
    capture: MemoryCapture,
    status: str | None = None,
    source_turn_id: str | None = None,
) -> Memory | None:
    query = select(Memory).where(
        Memory.owner_id == owner_id,
        func.lower(Memory.content) == capture.text.casefold(),
        visible_in(conversation_id),
    )
    if status is not None:
        query = query.where(Memory.status == status)
    if source_turn_id is not None:
        query = query.where(Memory.source_turn_id == source_turn_id)
    return await db.scalar(query.order_by(Memory.updated_at.desc()).limit(1))


async def _indexed_contradictions(
    db: AsyncSession, *, owner_id: str, conversation_id: str, capture: MemoryCapture
) -> list[Memory]:
    """Active visible memories with the same resolved subject, using only the indexed lookup."""
    if capture.subject is None:
        return []
    return list(
        (
            await db.scalars(
                select(Memory)
                .where(
                    Memory.owner_id == owner_id,
                    Memory.status == "active",
                    Memory.subject == capture.subject,
                    visible_in(conversation_id),
                )
                .order_by(Memory.updated_at.desc(), Memory.id)
            )
        ).all()
    )


async def _contradictions(
    db: AsyncSession, *, owner_id: str, conversation_id: str, capture: MemoryCapture
) -> list[Memory]:
    rows = await _indexed_contradictions(db, owner_id=owner_id, conversation_id=conversation_id, capture=capture)
    if capture.subject is None:
        return []
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
                    visible_in(conversation_id),
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


async def proposal_conflicts(db: AsyncSession, *, token: str, secret: str, owner_id: str, conversation_id: str) -> bool:
    """Whether a verified proposal contradicts a fact the owner already has saved.

    A contradiction changes what is stored (the old fact is replaced, kept as past, or both stay), which only the user
    can decide, so every mode asks. A scan that could not finish counts as a conflict: fail closed, ask.
    """
    capture, _ = _verified(token, secret, owner_id, conversation_id)
    try:
        return bool(await _contradictions(db, owner_id=owner_id, conversation_id=conversation_id, capture=capture))
    except MemoryCaptureError:
        return True


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
    claim = await _claim_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="discard",
    )
    if claim is None:
        raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision="discard")
    if claim.replay is not None:
        return _replay_response(claim.replay, "discard", payload)
    await _record_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="discard",
        outcome="discarded",
        result=response,
        lease_id=claim.lease_id,
    )
    return response


def proposal_summary(token: str, secret: str, owner_id: str, conversation_id: str) -> tuple[str, str, bool, bool]:
    """``(confirmation_id, text, explicit, negated)`` of a verified proposal, for callers outside this module."""
    capture, payload = _verified(token, secret, owner_id, conversation_id)
    negated = bool(capture.negated)
    if "negated" not in payload:
        # A token minted before the claim existed: re-derive it from the signed text with the scoped parser rather
        # than trusting a missing claim as "not negated".
        reparsed = parse_capture(capture.text)
        negated = negated or bool(reparsed is not None and reparsed.negated)
    return str(payload["confirmation_id"]), capture.text, bool(capture.explicit), negated


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

    Consent is bound to ``(confirmation_id, revision)``. The proposal row's
    state is authoritative: a valid signature alone can no longer write, and a
    decided proposal can never be resurrected by replaying its token.
    """
    if not secret:
        raise MemoryCaptureError("MEMORY_CAPTURE_NOT_CONFIGURED", 503)
    capture, payload = _verified(token, secret, owner_id, conversation_id)
    if is_referential(capture.text):
        # A confirmation/rejection may never also be stored as a bare reference.
        raise MemoryCaptureError("MEMORY_REFERENTIAL_CAPTURE", 422)
    proposed_turn = payload.get("source_turn_id")
    if not isinstance(proposed_turn, str) or not proposed_turn:
        raise MemoryCaptureError("PROPOSAL_INVALID", 400)
    confirmation_id = payload["confirmation_id"]
    revision = int(payload["revision"])

    replayed = await _resolved_proposal_response(
        db, owner_id=owner_id, confirmation_id=confirmation_id, decision=decision
    )
    if replayed is not None:
        return replayed

    existing = await _decision(db, confirmation_id, revision, owner_id)
    if existing is not None:
        if existing.outcome == "applying":
            if not lease_expired(existing.lease_expires_at):
                raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision=existing.decision)
        elif existing.outcome != "awaiting_confirmation":
            await _finish_proposal(
                db,
                owner_id=owner_id,
                confirmation_id=confirmation_id,
                state="saved" if decision != "discard" else "discarded",
                decision=decision,
                result=_replay_response(existing, decision, payload),
            )
            await db.commit()
            return _replay_response(existing, decision, payload)

    await _ensure_proposal(db, owner_id=owner_id, conversation_id=conversation_id, payload=payload, token=token)
    if not await _claim_proposal(db, owner_id=owner_id, confirmation_id=confirmation_id, decision=decision):
        await db.rollback()
        raise MemoryCaptureError("MEMORY_PROPOSAL_RESOLVED", 409, confirmation_id=confirmation_id)

    try:
        result = await _apply_consent_decision(
            db,
            memory_service,
            decision=decision,
            owner_id=owner_id,
            conversation_id=conversation_id,
            proposed_turn=proposed_turn,
            source_turn_id=source_turn_id,
            capture=capture,
            payload=payload,
            token=token,
            secret=secret,
            existing=existing,
            revision=revision,
        )
    except Exception:
        await _release_proposal(db, owner_id=owner_id, confirmation_id=confirmation_id)
        raise
    await _finish_proposal(
        db,
        owner_id=owner_id,
        confirmation_id=confirmation_id,
        state="discarded" if decision == "discard" else "saved",
        decision=decision,
        result=result,
    )
    await db.commit()
    return result


async def _apply_consent_decision(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    decision: str,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    source_turn_id: str | None,
    capture: MemoryCapture,
    payload: dict,
    token: str,
    secret: str,
    existing,
    revision: int,
) -> dict:
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
        async with _advisory_subject_lock(db, owner_id, capture.subject):
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
        return await _clarification_save(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            capture=capture,
            payload=payload,
            token=token,
            decision=decision,
        )

    async with _advisory_subject_lock(db, owner_id, capture.subject):
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


async def _clarification_save(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    payload: dict,
    token: str,
    decision: str,
) -> dict:
    """A clarification revision is consent for one replacement, not a fresh save."""
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
    applied = await _exact_content(
        db, owner_id=owner_id, conversation_id=conversation_id, capture=capture, status="active"
    )
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
        if existing.outcome == "applying":
            if not lease_expired(existing.lease_expires_at) or existing.decision != "replace":
                raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision=existing.decision)
        elif existing.outcome != "awaiting_confirmation":
            raise MemoryCaptureError("DECISION_ALREADY_FINAL", 409, outcome=existing.outcome)
        elif existing.target_id != target_id or existing.target_version != target_version:
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


def _temporary_in_chat(row: Memory | None, conversation_id: str) -> Memory | None:
    if row is not None and row.scope == SCOPE_CONVERSATION and row.source_conversation_id == conversation_id:
        return row
    return None


async def _retire_temporary(db: AsyncSession, owner_id: str, row: Memory) -> None:
    """Permanent wins: quietly tombstone a temporary duplicate without touching unbound proposals."""
    await delete_memory(db, owner_id, row.id, scrub_proposals=False, conversation_id=row.source_conversation_id)


async def _visible_temporary_duplicate(
    db: AsyncSession, *, owner_id: str, conversation_id: str, capture: MemoryCapture
) -> Memory | None:
    statement = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.status == "active",
        Memory.scope == SCOPE_CONVERSATION,
        Memory.source_conversation_id == conversation_id,
    )
    content_match = func.lower(Memory.content) == capture.text.casefold()
    condition = content_match
    if capture.subject is not None:
        condition = or_(Memory.subject == capture.subject, content_match)
    return await db.scalar(statement.where(condition).order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(1))


async def _reconcile_promotion_conflict(
    service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    memory_id: str,
    capture: MemoryCapture,
) -> tuple[dict | None, list[str]]:
    """Re-read a row whose in-place UPDATE lost its race, under the conversation and owner locks.

    Returns an ``already_saved`` response when nothing may be written (hidden/deleted, global, or a different fact),
    or the ids of active temporary rows of this subject that the caller must quietly retire after writing.
    """
    async with service.sessions() as db, db.begin():
        await owned_conversation(db, owner_id, conversation_id, lock=True, key_share=True)
        await lock_owner_memories(db, owner_id)
        row = await db.scalar(
            select(Memory)
            .where(Memory.id == memory_id, Memory.owner_id == owner_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return {"status": "already_saved", "memory": None}, []
        if row.status not in {"active", "superseded"}:
            return {"status": "already_saved", "memory": asdict(memory_result(row))}, []
        content_match = func.lower(Memory.content) == capture.text.casefold()
        condition = or_(Memory.subject == capture.subject, content_match) if capture.subject else content_match
        active = list(
            (
                await db.scalars(
                    select(Memory).where(
                        Memory.owner_id == owner_id,
                        Memory.status == "active",
                        visible_in(conversation_id),
                        condition,
                    )
                )
            ).all()
        )
        if row.status == "superseded":
            # The row moved on. A concurrent replacement may already be the one active permanent fact; otherwise
            # the ordinary create path writes it and the active temporary copies are quietly retired.
            permanent = next((item for item in active if item.scope == SCOPE_GLOBAL), None)
            if permanent is not None:
                return {"status": "already_saved", "memory": asdict(memory_result(permanent))}, []
            return None, [item.id for item in active if item.scope == SCOPE_CONVERSATION]
        if row.scope != SCOPE_CONVERSATION or row.source_conversation_id != conversation_id:
            return {"status": "already_saved", "memory": asdict(memory_result(row))}, []
        permanent = next((item for item in active if item.scope == SCOPE_GLOBAL), None)
        if permanent is not None:
            return {"status": "already_saved", "memory": asdict(memory_result(permanent))}, []
        temporary_rows = [item.id for item in active if item.scope == SCOPE_CONVERSATION]
        if temporary_rows:
            return None, temporary_rows
        return {"status": "already_saved", "memory": asdict(memory_result(row))}, []


async def _promote_or_create(
    db: AsyncSession,
    memory_service: MemoryService,
    *,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    source_turn_id: str | None,
    capture: MemoryCapture,
    temporary: Memory | None,
    conflicting: list[Memory],
    key: str,
    guard,
) -> tuple[object | None, bool, dict | None]:
    """Promote the visible temporary row in place, or create a normal permanent row when there is none."""
    if temporary is not None and not conflicting:
        try:
            result = await promote_temporary_memory(
                db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                memory_id=temporary.id,
                expected_version=temporary.version,
                content=capture.text,
                subject=capture.subject,
                embedder=memory_service.embedder,
                guard=guard,
            )
            return result, True, None
        except PromotionConflict:
            handled, retire_ids = await _reconcile_promotion_conflict(
                memory_service,
                owner_id=owner_id,
                conversation_id=conversation_id,
                memory_id=temporary.id,
                capture=capture,
            )
            if handled is not None:
                return None, False, handled
            result = await memory_service.create_memory(
                owner_id,
                conversation_id,
                source_turn_id or proposed_turn,
                capture.text,
                key,
                guard=guard,
            )
            for identifier in retire_ids:
                await delete_memory(db, owner_id, identifier, scrub_proposals=False, conversation_id=conversation_id)
            return result, False, None
    result = await memory_service.create_memory(
        owner_id,
        conversation_id,
        source_turn_id or proposed_turn,
        capture.text,
        key,
        guard=guard,
    )
    return result, False, None


def _conflict_rows(rows: list[Memory], capture: MemoryCapture) -> list[Memory]:
    return [
        row
        for row in rows
        if (parsed := parse_capture(row.content)) is not None
        and (parsed.value != capture.value or parsed.negated != capture.negated)
    ]


async def _arm_clarification(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    payload: dict,
    secret: str,
    target: Memory,
) -> tuple[str, int]:
    """Mint and record one clarification revision, returning its signed token and revision."""
    clarified, revision = _clarification_token(payload, secret, target=target)
    revised = {**payload, "revision": revision, "target_id": target.id, "target_version": target.version}
    prior = await _decision(db, payload["confirmation_id"], revision, owner_id)
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
    return clarified, revision


async def _clarify(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    payload: dict,
    secret: str,
    target: Memory,
) -> None:
    clarified, revision = await _arm_clarification(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        capture=capture,
        payload=payload,
        secret=secret,
        target=target,
    )
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
    active_same = await _exact_content(
        db, owner_id=owner_id, conversation_id=conversation_id, capture=capture, status="active"
    )
    temporary = _temporary_in_chat(active_same, conversation_id)
    if active_same is not None and temporary is None:
        duplicate_temp = await _visible_temporary_duplicate(
            db, owner_id=owner_id, conversation_id=conversation_id, capture=capture
        )
        if duplicate_temp is not None:
            await _retire_temporary(db, owner_id, duplicate_temp)
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
    replay = await _exact_content(
        db, owner_id=owner_id, conversation_id=conversation_id, capture=capture, source_turn_id=proposed_turn
    )
    temporary = temporary or _temporary_in_chat(replay, conversation_id)
    if replay is not None and temporary is None:
        duplicate_temp = await _visible_temporary_duplicate(
            db, owner_id=owner_id, conversation_id=conversation_id, capture=capture
        )
        if duplicate_temp is not None:
            await _retire_temporary(db, owner_id, duplicate_temp)
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
    previous = await _contradictions(db, owner_id=owner_id, conversation_id=conversation_id, capture=capture)
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
            secret=secret,
            target=conflicting[0],
        )
    # An explicit permanent save over a temporary fact of this chat promotes that same row in place: the user
    # asked for a permanent memory, so no second row is created.
    temporary = temporary or next(
        (row for row in previous if _temporary_in_chat(row, conversation_id) is not None), None
    )
    permanent_same = next((row for row in previous if row.scope == SCOPE_GLOBAL), None)
    if temporary is not None and not conflicting and permanent_same is not None:
        # A permanent fact already carries this subject and value: keep it and retire the duplicate temporary row.
        await _retire_temporary(db, owner_id, temporary)
        response = {"status": "already_saved", "memory": asdict(memory_result(permanent_same))}
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
    claim = await _claim_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="save",
    )
    if claim is None:
        raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision="save")
    if claim.replay is not None:
        return _replay_response(claim.replay, "save", payload)
    await _require_lease(db, payload, claim.lease_id)
    try:
        result, promoted, handled = await _promote_or_create(
            db,
            memory_service,
            owner_id=owner_id,
            conversation_id=conversation_id,
            proposed_turn=proposed_turn,
            source_turn_id=source_turn_id,
            capture=capture,
            temporary=temporary,
            conflicting=conflicting,
            key=key,
            guard=partial(_require_lease, payload=payload, lease_id=claim.lease_id, lock=True),
        )
    except Exception:
        # No memory effect was observed; release the claim so a retry can
        # proceed. A crash (not an observed failure) leaves it ``applying``.
        await _release_claim(db, payload=payload, decision="save", lease_id=claim.lease_id)
        raise
    response = handled if handled is not None else {"status": "saved", "memory": asdict(result)}
    if promoted:
        response["promoted"] = True
    await _record_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="save",
        outcome="saved",
        result=response,
        lease_id=claim.lease_id,
    )
    return response


async def _committed_retraction(db: AsyncSession, *, owner_id: str, target_id: str, target_version: int) -> bool:
    """A retraction has no new row; its receipt is the superseded target version."""
    previous = await db.scalar(select(Memory).where(Memory.id == target_id, Memory.owner_id == owner_id))
    return previous is not None and previous.status == "superseded" and previous.version == target_version + 1


async def _retract_target(
    db: AsyncSession,
    *,
    payload: dict,
    owner_id: str,
    conversation_id: str,
    token: str,
    target_id: str,
    target_version: int,
    target: Memory,
    lease_id: str | None,
) -> dict:
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
    try:
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
            lease_id=lease_id,
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return response


async def _replace_or_promote(
    memory_service: MemoryService,
    *,
    db: AsyncSession,
    owner_id: str,
    conversation_id: str,
    proposed_turn: str,
    source_turn_id: str | None,
    capture: MemoryCapture,
    target: Memory,
    target_id: str,
    target_version: int,
    key: str,
    guard,
) -> tuple[object | None, bool, dict | None]:
    """Replace the target; a temporary target is promoted in place on the same row."""
    if target.scope == SCOPE_CONVERSATION and target.source_conversation_id == conversation_id:
        try:
            result = await promote_temporary_memory(
                db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                memory_id=target_id,
                expected_version=target_version,
                content=capture.text,
                subject=capture.subject,
                embedder=memory_service.embedder,
                guard=guard,
            )
            return result, True, None
        except PromotionConflict:
            handled, retire_ids = await _reconcile_promotion_conflict(
                memory_service,
                owner_id=owner_id,
                conversation_id=conversation_id,
                memory_id=target_id,
                capture=capture,
            )
            if handled is not None:
                return None, False, handled
            result = await memory_service.correct_memory(
                owner_id,
                target_id,
                target_version,
                capture.text,
                key,
                source_turn_id=source_turn_id or proposed_turn,
                conversation_id=conversation_id,
                scope=SCOPE_GLOBAL,
                guard=guard,
            )
            for identifier in retire_ids:
                await delete_memory(db, owner_id, identifier, scrub_proposals=False, conversation_id=conversation_id)
            return result, False, None
    result = await memory_service.correct_memory(
        owner_id,
        target_id,
        target_version,
        capture.text,
        key,
        source_turn_id=source_turn_id or proposed_turn,
        conversation_id=conversation_id,
        scope=SCOPE_GLOBAL,
        guard=guard,
    )
    return result, False, None


async def _replace_recovery(
    db: AsyncSession,
    *,
    owner_id: str,
    conversation_id: str,
    capture: MemoryCapture,
    payload: dict,
    token: str,
    target: Memory | None,
    target_id: str,
    target_version: int,
) -> dict | None:
    """Report an effect that already committed before its decision receipt did."""
    applied = await _exact_content(
        db, owner_id=owner_id, conversation_id=conversation_id, capture=capture, status="active"
    )
    if applied is not None and applied.supersedes_id == target_id:
        response = {"status": "corrected", "memory": asdict(memory_result(applied))}
    elif (
        target is not None
        and target.version == target_version + 1
        and target.scope == SCOPE_GLOBAL
        and target.content == capture.text
    ):
        # An in-place promotion is the same row with a bumped version and no superseded row.
        response = {"status": "corrected", "memory": asdict(memory_result(target)), "promoted": True}
    elif capture.negated and await _committed_retraction(
        db, owner_id=owner_id, target_id=target_id, target_version=target_version
    ):
        response = {"status": "retracted", "memory": None}
    else:
        return None
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
        select(Memory).where(
            Memory.id == target_id,
            Memory.owner_id == owner_id,
            Memory.status == "active",
            visible_in(conversation_id),
        )
    )
    recovered = await _replace_recovery(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        capture=capture,
        payload=payload,
        token=token,
        target=target,
        target_id=target_id,
        target_version=target_version,
    )
    if recovered is not None:
        return recovered
    if target is None:
        raise MemoryCaptureError("TARGET_CHANGED", 409, target_id=target_id)
    if recovery_only:
        # Without an awaiting confirmation row, only a previously committed
        # effect may be reported. A missing row never authorizes a new write.
        raise MemoryCaptureError("MEMORY_FRESH_CONFIRMATION_REQUIRED", 409, target_id=target_id)
    if target.version != target_version:
        raise MemoryCaptureError("TARGET_CHANGED", 409, target_id=target_id)
    # Validate before claiming so an already-stale clarification is not turned
    # into an in-progress claim; the correction itself still re-checks version.
    claim = await _claim_decision(
        db,
        payload=payload,
        owner_id=owner_id,
        conversation_id=conversation_id,
        token=token,
        decision="replace",
        target=target,
    )
    if claim is None:
        raise MemoryCaptureError("MEMORY_DECISION_IN_PROGRESS", 409, decision="replace")
    if claim.replay is not None:
        return _replay_response(claim.replay, "replace", payload)
    await _require_lease(db, payload, claim.lease_id)
    try:
        if capture.negated:
            return await _retract_target(
                db,
                payload=payload,
                owner_id=owner_id,
                conversation_id=conversation_id,
                token=token,
                target_id=target_id,
                target_version=target_version,
                target=target,
                lease_id=claim.lease_id,
            )
        key = _request_key(owner_id, f"{proposed_turn}:replace:{payload['revision']}", capture.text)
        previous_content, previous_subject = target.content, target.subject
        try:
            result, promoted, handled = await _replace_or_promote(
                memory_service,
                db=db,
                owner_id=owner_id,
                conversation_id=conversation_id,
                proposed_turn=proposed_turn,
                source_turn_id=source_turn_id,
                capture=capture,
                target=target,
                target_id=target_id,
                target_version=target_version,
                key=key,
                guard=partial(_require_lease, payload=payload, lease_id=claim.lease_id, lock=True),
            )
        except HTTPException as exc:
            if (
                exc.status_code == 409
                and isinstance(exc.detail, dict)
                and exc.detail.get("code") == "MEMORY_VERSION_CONFLICT"
            ):
                raise MemoryCaptureError("TARGET_CHANGED", 409, target_id=target_id) from exc
            raise
        response = handled if handled is not None else {"status": "corrected", "memory": asdict(result)}
        if promoted:
            response["promoted"] = True
            # A replace that promoted in place must be undoable back to the previous fact.
            response["previous_content"] = previous_content
            response["previous_subject"] = previous_subject
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
            lease_id=claim.lease_id,
        )
        return response
    except Exception:
        # The observed failure happened before a memory effect; restore the
        # clarification so a retry can decide again. A crashed process leaves
        # the claim ``applying`` and retries fail closed with 409.
        await _release_claim(db, payload=payload, decision="replace", lease_id=claim.lease_id, awaiting=True)
        raise


__all__ = [
    "MemoryCaptureError",
    "confirm_memory",
    "pending_proposal",
    "propose_memory",
    "resolve_proposal",
]
