"""The fixed onboarding greeting uses real TTS inside the character ledger."""

import asyncio

import main as worker


class Handle:
    def __init__(self):
        self.played = False

    async def wait_for_playout(self):
        self.played = True


class Backend:
    def __init__(self, reserve_status=200):
        self.calls = []
        self.reserve_status = reserve_status

    async def post(self, path, json=None):
        self.calls.append((path, json))
        return type("Response", (), {"status_code": self.reserve_status})()


class Session:
    def __init__(self, error=None):
        self.error = error
        self.spoken = []
        self.handle = Handle()

    def say(self, text, allow_interruptions=True, add_to_chat_ctx=True):
        if self.error:
            raise RuntimeError("TTS_DOWN")
        self.spoken.append((text, allow_interruptions, add_to_chat_ctx))
        return self.handle


def test_greeting_reserves_characters_then_marks_played():
    backend = Backend()
    session = Session()
    asyncio.run(worker.speak_onboarding_greeting(backend, session, "Hey, I'm Sarjy."))
    assert backend.calls[0] == ("/internal/development/reserve", {"provider": "elevenlabs", "characters": 15})
    assert session.spoken == [("Hey, I'm Sarjy.", True, False)]
    assert session.handle.played is True
    assert backend.calls[-1][0] == "/internal/agent/onboarding/greeting-played"


def test_failed_reservation_skips_tts_and_does_not_mark_played():
    backend = Backend(reserve_status=429)
    session = Session()
    asyncio.run(worker.speak_onboarding_greeting(backend, session, "Hey"))
    assert session.spoken == []
    assert [path for path, _ in backend.calls] == ["/internal/development/reserve"]


def test_failed_tts_leaves_the_greeting_unplayed_for_a_retry():
    backend = Backend()
    session = Session(error=RuntimeError("TTS_DOWN"))
    asyncio.run(worker.speak_onboarding_greeting(backend, session, "Hey"))
    assert [path for path, _ in backend.calls] == ["/internal/development/reserve"]
