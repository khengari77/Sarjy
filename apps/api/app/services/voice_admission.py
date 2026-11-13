"""Durable, owner-scoped idempotency for voice-session admission.

A caller supplies an optional ``request_key``.  The key is bound to one
normalized start intent: conversation, language, voice, resolved tier and
preference.  The first request claims the key before quota checks and dispatch;
later requests with the same intent replay the stored outcome instead of
creating another session or dispatching again.  A changed intent is a typed
conflict.  Terminal outcomes are never converted into a replacement dispatch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.contracts import ResponsePreference
from app.infrastructure.database import VoiceAdmission, VoiceSession, now
from app.infrastructure.upsert import insert_ignore

# A claim older than this never dispatched a replacement: its process may have
# died at any point, so retries are conservative (uncertain, no new dispatch).
CLAIM_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class AdmissionClaim:
    """Either the winning claim id or a complete replay payload."""

    admission_id: str | None = None
    payload: dict | None = None


def intent_fingerprint(
    *,
    conversation_id: str,
    language: str,
    voice_id: str | None,
    tier: int,
    preference: ResponsePreference,
) -> str:
    normalized = json.dumps(
        {
            "conversation_id": conversation_id,
            "language": language,
            "voice_id": voice_id,
            "tier": tier,
            "preference": preference.value,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


def session_payload(voice: VoiceSession, config: Settings, owner_id: str) -> dict:
    from livekit import api

    token = (
        api.AccessToken(config.livekit_api_key, config.livekit_api_secret)
        .with_identity(owner_id)
        .with_ttl(timedelta(minutes=10))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=voice.room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )
    return {"id": voice.id, "url": config.livekit_url, "token": token, "expires_at": voice.expires_at}


async def begin_admission(
    db: AsyncSession,
    *,
    owner_id: str,
    request_key: str,
    fingerprint: str,
    conversation_id: str,
    config: Settings,
) -> AdmissionClaim:
    """Claim one intent or replay its durable outcome.

    The claim is committed before the caller checks quotas or dispatches, so a
    concurrent retry observes a stable admission instead of starting a second
    one.
    """
    reception_id = str(uuid4())
    inserted = await insert_ignore(
        db,
        VoiceAdmission,
        {
            "id": reception_id,
            "owner_id": owner_id,
            "request_key": request_key,
            "intent_hash": fingerprint,
            "conversation_id": conversation_id,
            "state": "in_progress",
            "created_at": now(),
            "updated_at": now(),
        },
        index_elements=["owner_id", "request_key"],
    )
    if inserted.rowcount == 1:
        await db.commit()
        return AdmissionClaim(admission_id=reception_id)

    existing = await db.scalar(
        select(VoiceAdmission).where(
            VoiceAdmission.owner_id == owner_id,
            VoiceAdmission.request_key == request_key,
        )
    )
    if existing is None:
        raise HTTPException(503, detail={"code": "VOICE_ADMISSION_UNAVAILABLE"})
    if existing.intent_hash != fingerprint:
        raise HTTPException(409, detail={"code": "VOICE_ADMISSION_CONFLICT"})
    return AdmissionClaim(payload=await _replay(db, existing, owner_id, config))


async def _replay(db: AsyncSession, admission: VoiceAdmission, owner_id: str, config: Settings) -> dict:
    state = admission.state
    if state == "in_progress":
        age = (datetime.now(UTC) - datetime.fromisoformat(admission.created_at)).total_seconds()
        if age < CLAIM_TIMEOUT_SECONDS:
            raise HTTPException(409, detail={"code": "VOICE_ADMISSION_IN_PROGRESS"})
        await _set_state(db, admission.id, "uncertain", "VOICE_ADMISSION_UNCERTAIN", 503)
        raise HTTPException(503, detail={"code": "VOICE_ADMISSION_UNCERTAIN"})
    if state in {"failed", "uncertain"}:
        code = admission.error_code or "VOICE_ADMISSION_FAILED"
        raise HTTPException(admission.error_status or 503, detail={"code": code})
    if state == "dispatched":
        return await _replay_dispatched(db, admission, owner_id, config)
    if state == "ended":
        raise HTTPException(409, detail={"code": "VOICE_ADMISSION_ENDED"})
    if state == "expired":
        raise HTTPException(410, detail={"code": "VOICE_ADMISSION_EXPIRED"})
    raise HTTPException(503, detail={"code": "VOICE_ADMISSION_UNAVAILABLE"})


async def _replay_dispatched(db: AsyncSession, admission: VoiceAdmission, owner_id: str, config: Settings) -> dict:
    voice = await db.get(VoiceSession, admission.session_id) if admission.session_id else None
    if voice is None or voice.owner_id != owner_id:
        raise HTTPException(503, detail={"code": "VOICE_ADMISSION_UNAVAILABLE"})
    if voice.status in {"starting", "active"} and voice.expires_at > datetime.now(UTC).isoformat():
        # Minting a new token is local; it is not a second dispatch.
        return session_payload(voice, config, owner_id)
    if voice.status == "failed":
        raise HTTPException(503, detail={"code": "VOICE_DISPATCH_FAILED"})
    terminal = "expired" if voice.expires_at <= datetime.now(UTC).isoformat() else "ended"
    await _set_state(
        db, admission.id, terminal, f"VOICE_ADMISSION_{terminal.upper()}", 410 if terminal == "expired" else 409
    )
    raise HTTPException(
        410 if terminal == "expired" else 409,
        detail={"code": f"VOICE_ADMISSION_{terminal.upper()}"},
    )


async def _set_state(
    db: AsyncSession,
    admission_id: str,
    state: str,
    error_code: str | None = None,
    error_status: int | None = None,
) -> None:
    await db.execute(
        update(VoiceAdmission)
        .where(VoiceAdmission.id == admission_id)
        .values(state=state, error_code=error_code, error_status=error_status, updated_at=now())
    )
    await db.commit()


async def bind_session(db: AsyncSession, admission_id: str, session_id: str) -> None:
    """Attach the created session without committing the caller's transaction."""
    await db.execute(
        update(VoiceAdmission).where(VoiceAdmission.id == admission_id).values(session_id=session_id, updated_at=now())
    )


async def mark_dispatched(db: AsyncSession, admission_id: str) -> None:
    await _set_state(db, admission_id, "dispatched")


async def mark_failed(db: AsyncSession, admission_id: str, *, code: str, status: int, uncertain: bool = False) -> None:
    await _set_state(db, admission_id, "uncertain" if uncertain else "failed", code, status)


async def mark_session_outcome(db: AsyncSession, session_id: str, state: str) -> None:
    """Project a session terminal state onto its admission, without committing."""
    await db.execute(
        update(VoiceAdmission)
        .where(VoiceAdmission.session_id == session_id, VoiceAdmission.state == "dispatched")
        .values(state=state, updated_at=now())
    )
