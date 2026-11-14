"""Live voice-control delivery and ACK fencing.

The API authorizes and signs a change; the worker applies it and ACKs. This
module owns the shell around that exchange: delivering the signed control to
the exact LiveKit room, recording the worker's ACK, and deriving the confirmed
active voice. It never mutates TTS itself.
"""

from datetime import UTC, datetime, timedelta

from livekit import api
from livekit.protocol.models import DataPacket
from sqlalchemy import select, update

from app.domain.voice_control import CONTROL_TOPIC, sign_voice_control, voice_control_payload
from app.infrastructure.database import VoiceControl, VoiceSession, now

# Bounds a control that never reaches a synthesis boundary (worker crash, lost
# ACK). At expiry the previous active voice stays and the request fails closed.
CONTROL_TTL_SECONDS = 300


def control_expiry() -> str:
    return (datetime.now(UTC) + timedelta(seconds=CONTROL_TTL_SECONDS)).isoformat()


def _is_expired(control: VoiceControl) -> bool:
    report = control.expires_at
    return not report or report <= now()


class VoiceControlError(Exception):
    """Delivery failure with an actionable, non-secret error code."""

    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code = code
        self.status = status


class RoomVoiceControl:
    """Send the signed control to a room with LiveKit's server API."""

    def __init__(self, *, url: str, api_key: str, api_secret: str):
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret

    async def send(self, *, room_name: str, token: str) -> None:
        if not (self.url and self.api_key and self.api_secret):
            raise VoiceControlError("VOICE_CONTROL_UNAVAILABLE")
        try:
            async with api.LiveKitAPI(self.url, self.api_key, self.api_secret) as lk:
                await lk.room.send_data(
                    api.SendDataRequest(
                        room=room_name,
                        data=token.encode(),
                        topic=CONTROL_TOPIC,
                        kind=DataPacket.Kind.RELIABLE,
                    )
                )
        except Exception as exc:  # provider/SDK boundary: normalize to an actionable code
            raise VoiceControlError("VOICE_CONTROL_UNAVAILABLE") from exc


def session_active_voice(voice: VoiceSession) -> str | None:
    return voice.active_voice_id or (voice.pipeline or {}).get("tts", {}).get("voice_id")


def _pipeline_revision(voice: VoiceSession) -> int:
    return voice.active_settings_revision or 0


def voice_state(voice: VoiceSession, control: VoiceControl | None) -> dict:
    """Public projection: confirmed active voice plus the latest control."""
    return {
        "session_id": voice.id,
        "conversation_id": voice.conversation_id,
        "active_voice_id": session_active_voice(voice),
        "active_settings_revision": _pipeline_revision(voice),
        "request_id": control.request_id if control else None,
        "voice_id": control.voice_id if control else None,
        "settings_revision": control.settings_revision if control else None,
        "status": control.status if control else "idle",
        "error_code": control.error_code if control else None,
    }


async def latest_control(db, session_id: str) -> VoiceControl | None:
    control = await db.scalar(
        select(VoiceControl)
        .where(VoiceControl.session_id == session_id)
        .order_by(VoiceControl.created_at.desc(), VoiceControl.settings_revision.desc())
        .limit(1)
    )
    if control is not None:
        control = await expire_control(db, control)
    return control


async def expire_control(db, control: VoiceControl) -> VoiceControl:
    """Fail a stuck non-terminal control once its claim window has passed."""
    if control.status in {"pending", "dispatched"} and _is_expired(control):
        await db.execute(
            update(VoiceControl)
            .where(VoiceControl.id == control.id, VoiceControl.status.in_(["pending", "dispatched"]))
            .values(status="failed", error_code="VOICE_SWITCH_EXPIRED", updated_at=now())
        )
        await db.commit()
        await db.refresh(control)
    return control


async def fail_session_controls(db, session_id: str, code: str = "VOICE_SESSION_ENDED") -> None:
    """Settle anything still in flight when its room/session ends."""
    await db.execute(
        update(VoiceControl)
        .where(
            VoiceControl.session_id == session_id,
            VoiceControl.status.in_(["pending", "dispatched"]),
        )
        .values(status="failed", error_code=code, updated_at=now())
    )


async def deliver_control(db, *, control_id: str, delivery, secret: str) -> dict:
    """Deliver a freshly authorized control exactly once.

    The pending -> dispatched claim is the idempotency fence: replays and
    concurrent submissions see the claimed row and never resend.
    """
    control = await db.get(VoiceControl, control_id)
    if control is None:
        raise VoiceControlError("VOICE_CONTROL_UNKNOWN", 404)
    control = await expire_control(db, control)
    voice = await db.get(VoiceSession, control.session_id)
    if voice is None:
        raise VoiceControlError("VOICE_SESSION_NOT_FOUND", 404)
    if control.status == "pending":
        claimed = await db.execute(
            update(VoiceControl)
            .where(VoiceControl.id == control.id, VoiceControl.status == "pending")
            .values(status="dispatched", updated_at=now())
        )
        await db.commit()
        if claimed.rowcount:
            try:
                token = sign_voice_control(
                    voice_control_payload(
                        session_id=voice.id,
                        room_name=voice.room_name,
                        conversation_id=voice.conversation_id,
                        owner_id=control.owner_id,
                        voice_id=control.voice_id,
                        request_id=control.request_id,
                        settings_revision=control.settings_revision,
                        expires_at=control.expires_at,
                    ),
                    secret,
                )
            except ValueError:
                await _fail_control(db, control.id, "VOICE_CONTROL_UNAVAILABLE")
                token = None
            if token is not None:
                try:
                    await delivery.send(room_name=voice.room_name, token=token)
                except VoiceControlError as exc:
                    await _fail_control(db, control.id, exc.code)
        await db.refresh(control)
    return voice_state(voice, control)


async def _fail_control(db, control_id: str, code: str) -> None:
    await db.execute(
        update(VoiceControl)
        .where(VoiceControl.id == control_id, VoiceControl.status == "dispatched")
        .values(status="failed", error_code=code, updated_at=now())
    )
    await db.commit()


def _ack_fingerprint(control: VoiceControl) -> tuple[str, str, int]:
    return (control.request_id, control.voice_id, control.settings_revision)


def _ack_matches(control: VoiceControl, *, request_id: str, voice_id: str, settings_revision: int) -> bool:
    return _ack_fingerprint(control) == (request_id, voice_id, settings_revision)


async def apply_ack(
    db,
    *,
    voice: VoiceSession,
    request_id: str,
    voice_id: str,
    settings_revision: int,
    status: str,
    error_code: str | None,
) -> dict:
    """Record one worker ACK, ignoring stale and duplicate acknowledgements."""
    control = await db.scalar(
        select(VoiceControl).where(VoiceControl.session_id == voice.id, VoiceControl.request_id == request_id)
    )
    if control is None:
        raise VoiceControlError("VOICE_CONTROL_UNKNOWN", 404)
    if not _ack_matches(control, request_id=request_id, voice_id=voice_id, settings_revision=settings_revision):
        raise VoiceControlError("VOICE_CONTROL_MISMATCH", 409)
    # A stuck control expires before the ACK can activate it; expiry keeps the
    # previous voice active and settles as a failure.
    control = await expire_control(db, control)
    # Already settled: replaying the same ACK is harmless and must not reapply.
    if control.status in {"applied", "failed"}:
        await db.refresh(voice)
        return voice_state(voice, control)
    # A newer change already succeeded, or this request was superseded.
    if control.status == "superseded" or settings_revision < _pipeline_revision(voice):
        await db.refresh(voice)
        return voice_state(voice, control)

    if status == "applied":
        await db.execute(
            update(VoiceSession)
            .where(VoiceSession.id == voice.id)
            .values(active_voice_id=voice_id, active_settings_revision=settings_revision)
        )
        await db.execute(
            update(VoiceControl)
            .where(VoiceControl.id == control.id, VoiceControl.status.in_(["pending", "dispatched"]))
            .values(status="applied", error_code=None, updated_at=now())
        )
    else:
        # Keep the previous active voice; the selected future preference stays persisted.
        await db.execute(
            update(VoiceControl)
            .where(VoiceControl.id == control.id, VoiceControl.status.in_(["pending", "dispatched"]))
            .values(status="failed", error_code=error_code or "VOICE_SWITCH_FAILED", updated_at=now())
        )
    await db.commit()
    await db.refresh(voice)
    await db.refresh(control)
    return voice_state(voice, control)
