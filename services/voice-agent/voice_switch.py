"""Worker-side live voice switching at the next synthesis boundary.

The API signs every control with the shared worker secret. This module verifies
the signature and the exact session/room before touching TTS, keeps only the
newest pending change, applies it when the next utterance starts, and ACKs the
result so the UI can distinguish confirmed-active from selected-next-call.

No reconnect, no room change and no mid-utterance cut: ``update_options`` only
affects the next synthesis; already generated audio keeps playing.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx
from app.domain.voice_control import verify_voice_control

ACK_ATTEMPTS = 2


@dataclass(frozen=True)
class VoiceChange:
    request_id: str
    voice_id: str
    settings_revision: int


class VoiceSwitch:
    def __init__(
        self,
        *,
        tts,
        backend: httpx.AsyncClient,
        session_id: str,
        room_name: str,
        secret: str,
        active_voice_id: str | None,
        settings_revision: int,
    ):
        self.tts = tts
        self.backend = backend
        self.session_id = session_id
        self.room_name = room_name
        self.secret = secret
        self.active_voice_id = active_voice_id
        self.settings_revision = settings_revision
        self.pending: VoiceChange | None = None
        self.applied_request_id: str | None = None
        self._lock = asyncio.Lock()

    def receive(self, data: bytes) -> bool:
        """Verify and queue one control packet. Returns whether it was queued."""
        try:
            payload = verify_voice_control(data.decode(), self.secret)
        except (UnicodeDecodeError, ValueError):
            return False
        if payload["session_id"] != self.session_id or payload["room_name"] != self.room_name:
            return False
        change = VoiceChange(
            request_id=payload["request_id"],
            voice_id=payload["voice_id"],
            settings_revision=payload["settings_revision"],
        )
        # Stale or duplicate controls never touch the queued change.
        if change.settings_revision <= self.settings_revision:
            return False
        if change.request_id == self.applied_request_id:
            return False
        if self.pending is not None and change.settings_revision <= self.pending.settings_revision:
            return False
        # A newer change replaces an older unapplied one; the older request is
        # superseded server-side and its eventual ACK is ignored.
        self.pending = change
        return True

    async def apply_pending(self) -> bool:
        """Apply a queued change before the next utterance. Returns True if applied."""
        async with self._lock:
            change = self.pending
            if change is None:
                return False
            self.pending = None
            update = getattr(self.tts, "update_options", None)
            if update is None:
                await self._ack(change, "failed", "VOICE_SWITCH_UNSUPPORTED")
                return False
            try:
                update(voice_id=change.voice_id)
            except Exception:
                logging.exception("Voice switch failed; keeping the previous TTS voice")
                await self._ack(change, "failed", "VOICE_SWITCH_FAILED")
                return False
            self.active_voice_id = change.voice_id
            self.settings_revision = change.settings_revision
            self.applied_request_id = change.request_id
            await self._ack(change, "applied", None)
            return True

    async def _ack(self, change: VoiceChange, status: str, error_code: str | None) -> None:
        body = {
            "request_id": change.request_id,
            "voice_id": change.voice_id,
            "settings_revision": change.settings_revision,
            "status": status,
            "error_code": error_code,
        }
        for attempt in range(ACK_ATTEMPTS):
            try:
                response = await self.backend.post("/internal/agent/voice/ack", json=body)
                if response.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            if attempt + 1 < ACK_ATTEMPTS:
                await asyncio.sleep(0.1)
        logging.error("Voice control ACK could not be delivered")
