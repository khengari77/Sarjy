"""Signed server-to-worker voice control messages.

The API authorizes a voice change against the curated catalog and the exact
owner/session/room, then signs a small opaque token with the shared worker
secret. The worker only trusts tokens it can verify with that secret; a room
participant cannot forge a voice change merely by publishing data.

Pure domain code: no I/O, no database, no provider clients.
"""

import base64
import hashlib
import hmac
import json
from typing import Any

from app.domain.secrets import secret_matches

CONTROL_TOPIC = "sarjy.voice"
CONTROL_AUDIENCE = "sarjy.voice.change"
CONTROL_FIELDS = frozenset(
    {
        "audience",
        "session_id",
        "room_name",
        "conversation_id",
        "owner_id",
        "voice_id",
        "request_id",
        "settings_revision",
    }
)


def voice_control_payload(
    *,
    session_id: str,
    room_name: str,
    conversation_id: str,
    owner_id: str,
    voice_id: str,
    request_id: str,
    settings_revision: int,
) -> dict[str, Any]:
    """Build the exact payload the worker may act on."""
    return {
        "audience": CONTROL_AUDIENCE,
        "session_id": session_id,
        "room_name": room_name,
        "conversation_id": conversation_id,
        "owner_id": owner_id,
        "voice_id": voice_id,
        "request_id": request_id,
        "settings_revision": settings_revision,
    }


def sign_voice_control(payload: dict[str, Any], secret: str) -> str:
    """Encode the control payload as an HMAC-signed opaque token."""
    if not secret:
        raise ValueError("VOICE_CONTROL_NOT_CONFIGURED")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_voice_control(token: str, secret: str) -> dict[str, Any]:
    """Return the verified payload or raise ``ValueError``.

    Rejects foreign audiences, unknown keys, mistyped fields and non-positive
    revisions so a malformed control packet can never reach the TTS resource.
    """
    if not secret:
        raise ValueError("VOICE_CONTROL_NOT_CONFIGURED")
    try:
        body, signature = token.rsplit(".", 1)
    except (AttributeError, ValueError) as exc:
        raise ValueError("VOICE_CONTROL_INVALID") from exc
    try:
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    except UnicodeEncodeError as exc:
        raise ValueError("VOICE_CONTROL_INVALID") from exc
    if not secret_matches(signature, expected):
        raise ValueError("VOICE_CONTROL_INVALID")
    try:
        padded = body + "=" * (-len(body) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode())
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("VOICE_CONTROL_INVALID") from exc
    if not isinstance(payload, dict) or set(payload) != CONTROL_FIELDS:
        raise ValueError("VOICE_CONTROL_INVALID")
    if payload.get("audience") != CONTROL_AUDIENCE:
        raise ValueError("VOICE_CONTROL_INVALID")
    for name in ("session_id", "room_name", "conversation_id", "owner_id", "voice_id", "request_id"):
        if not isinstance(payload.get(name), str) or not payload[name]:
            raise ValueError("VOICE_CONTROL_INVALID")
    revision = payload.get("settings_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("VOICE_CONTROL_INVALID")
    return payload
