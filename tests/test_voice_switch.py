"""Worker-side voice switch: next-boundary apply, fencing, ACK and rollback."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from app.domain.voice_control import sign_voice_control, voice_control_payload
from voice_switch import VoiceChange, VoiceSwitch

SECRET = "worker-secret"


class FakeTTS:
    def __init__(self, *, fail=False):
        self.voice_id = "voice-a"
        self.calls = []
        self.fail = fail

    def update_options(self, *, voice_id):
        self.calls.append(voice_id)
        if self.fail:
            raise RuntimeError("provider rejected the voice")
        self.voice_id = voice_id


class Backend:
    def __init__(self, *, status=200):
        self.posts = []
        self.status = status

    async def post(self, path, json):
        self.posts.append((path, json))
        return httpx.Response(self.status)


def token(*, request_id="r1", voice="voice-b", revision=1, session="session", room="room", expires_at=None):
    return sign_voice_control(
        voice_control_payload(
            session_id=session,
            room_name=room,
            conversation_id="conversation",
            owner_id="owner",
            voice_id=voice,
            request_id=request_id,
            settings_revision=revision,
            expires_at=expires_at or (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        ),
        SECRET,
    ).encode()


def build(*, tts=None, backend=None, revision=0, voice="voice-a"):
    tts = tts or FakeTTS()
    backend = backend or Backend()
    switch = VoiceSwitch(
        tts=tts,
        backend=backend,
        session_id="session",
        room_name="room",
        secret=SECRET,
        active_voice_id=voice,
        settings_revision=revision,
    )
    return switch, tts, backend


def test_change_is_queued_then_applied_at_the_next_synthesis_boundary():
    switch, tts, backend = build()
    assert switch.receive(token()) is True
    # Nothing is applied merely because the packet arrived.
    assert tts.calls == []
    assert switch.active_voice_id == "voice-a"

    assert asyncio.run(switch.apply_pending()) is True
    assert tts.calls == ["voice-b"]
    assert switch.active_voice_id == "voice-b"
    assert switch.settings_revision == 1
    assert backend.posts == [
        (
            "/internal/agent/voice/ack",
            {
                "request_id": "r1",
                "voice_id": "voice-b",
                "settings_revision": 1,
                "status": "applied",
                "error_code": None,
            },
        )
    ]


def test_unsigned_tampered_and_foreign_controls_are_ignored():
    switch, tts, backend = build()
    tampered = token()[:-1] + (b"0" if token()[-1:] != b"0" else b"1")
    assert switch.receive(tampered) is False
    assert switch.receive(b"not-a-token") is False
    assert switch.receive(b"\xff\xfe") is False
    assert switch.receive(token(room="another-room")) is False
    assert switch.receive(token(session="another-session")) is False
    assert asyncio.run(switch.apply_pending()) is False
    assert tts.calls == []
    assert backend.posts == []


def test_duplicate_and_stale_controls_never_reapply():
    switch, tts, backend = build()
    assert switch.receive(token()) is True
    asyncio.run(switch.apply_pending())
    # Duplicate of the applied request, and an older revision, are fenced.
    assert switch.receive(token()) is False
    assert switch.receive(token(request_id="r0", voice="voice-a", revision=0)) is False
    assert asyncio.run(switch.apply_pending()) is False
    assert tts.calls == ["voice-b"]
    assert len(backend.posts) == 1


def test_concurrent_changes_apply_only_the_newest():
    switch, tts, backend = build()
    assert switch.receive(token(request_id="r1", voice="voice-b", revision=1)) is True
    assert switch.receive(token(request_id="r2", voice="voice-c", revision=2)) is True
    assert asyncio.run(switch.apply_pending()) is True
    assert tts.calls == ["voice-c"]
    assert switch.settings_revision == 2
    assert backend.posts[0][1]["request_id"] == "r2"


def test_tts_failure_keeps_the_previous_voice_and_acks_the_failure():
    tts = FakeTTS(fail=True)
    switch, _, backend = build(tts=tts)
    assert switch.receive(token()) is True
    assert asyncio.run(switch.apply_pending()) is False
    assert switch.active_voice_id == "voice-a"
    assert switch.settings_revision == 0
    assert backend.posts[0][1] == {
        "request_id": "r1",
        "voice_id": "voice-b",
        "settings_revision": 1,
        "status": "failed",
        "error_code": "VOICE_SWITCH_FAILED",
    }


def test_runtime_without_in_place_update_fails_closed():
    switch, _, backend = build(tts=object())
    assert switch.receive(token()) is True
    assert asyncio.run(switch.apply_pending()) is False
    assert backend.posts[0][1]["error_code"] == "VOICE_SWITCH_UNSUPPORTED"


def test_reconnect_starts_from_the_bootstrap_voice_and_ignores_old_controls():
    # A new session is a new VoiceSwitch built from the bootstrap voice and revision.
    switch, tts, backend = build(revision=2, voice="voice-c")
    assert switch.receive(token(request_id="old", voice="voice-b", revision=1)) is False
    assert switch.receive(token(request_id="new", voice="voice-d", revision=3)) is True
    asyncio.run(switch.apply_pending())
    assert tts.calls == ["voice-d"]
    assert backend.posts[0][1]["request_id"] == "new"


def test_ack_is_retried_after_a_transient_server_error():
    backend = Backend(status=503)
    switch, tts, _ = build(backend=backend)
    switch.receive(token())
    asyncio.run(switch.apply_pending())
    assert len(backend.posts) == 2
    assert json.loads(json.dumps(backend.posts[0][1]))["status"] == "applied"


def test_expired_control_is_never_queued_or_applied():
    switch, tts, backend = build()
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert switch.receive(token(expires_at=past)) is False
    assert asyncio.run(switch.apply_pending()) is False
    assert tts.calls == []
    assert backend.posts == []


def test_queued_control_that_expires_before_the_boundary_fails_closed():
    switch, tts, backend = build()
    switch.receive(token())
    switch.pending = VoiceChange(
        request_id="r1",
        voice_id="voice-b",
        settings_revision=1,
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    assert asyncio.run(switch.apply_pending()) is False
    assert tts.calls == []
    assert backend.posts[0][1]["status"] == "failed"
    assert backend.posts[0][1]["error_code"] == "VOICE_SWITCH_EXPIRED"
