import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from livekit import api as livekit_api

from app.config import Settings
from app.main import create_app
from app.services.voices import VoiceOption

AGENT_SECRET = 'test-agent-secret'


class FakeCatalog:
    async def require(self, voice_id):
        return VoiceOption(id=voice_id, name='Test Voice')


class FakeLiveKitAPI:
    """Records dispatch/room-deletion calls and can fail either boundary."""

    state: dict | None = None

    def __init__(self, *args, **kwargs):
        assert FakeLiveKitAPI.state is not None
        self.state = FakeLiveKitAPI.state
        self.agent_dispatch = SimpleNamespace(create_dispatch=self.dispatch)
        self.room = SimpleNamespace(delete_room=self.delete)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def dispatch(self, request):
        self.state['dispatched'].append(request.room)
        if self.state['dispatch_fails']:
            raise RuntimeError('dispatch unavailable')

    async def delete(self, request):
        self.state['deleted'].append(request.room)
        if self.state['delete_fails']:
            raise RuntimeError('room cleanup unavailable')


def build(tmp_path, monkeypatch, **overrides):
    state = {'dispatched': [], 'deleted': [], 'dispatch_fails': False, 'delete_fails': False}
    FakeLiveKitAPI.state = state
    monkeypatch.setattr(livekit_api, 'LiveKitAPI', FakeLiveKitAPI)
    settings = Settings(
        database_url=f'sqlite+aiosqlite:///{tmp_path}/lifecycle.db',
        openai_api_key='test-openai', deepseek_api_key='', deepgram_api_key='test-deepgram', eleven_api_key='test-eleven',
        livekit_url='https://fake.livekit', livekit_api_key='key' * 8, livekit_api_secret='secret' * 8,
        agent_service_secret=AGENT_SECRET, demo_calls_per_identity=10, _env_file=None, **overrides,
    )
    app = create_app(settings, voice_catalog=FakeCatalog())
    return app, TestClient(app), state, tmp_path / 'lifecycle.db'


def conversation(client):
    client.post('/v1/demo-session')
    return client.post('/v1/conversations').json()['id']


def start(client, conversation_id):
    return client.post('/v1/voice/sessions', json={'conversation_id': conversation_id, 'voice_id': 'test-voice'})


def reserve_ledger(client):
    response = client.post('/internal/development/reserve', json={'provider': 'openai'}, headers={'x-agent-secret': AGENT_SECRET})
    assert response.status_code == 200
    return response.json()['reserved_units']


def session_row(db_path, session_id):
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute('SELECT * FROM voice_sessions WHERE id=?', (session_id,)).fetchone()
    assert row is not None
    return dict(row)


def sessions_with_status(db_path, status):
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute('SELECT * FROM voice_sessions WHERE status=?', (status,))]


def expire(db_path, session_id):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            'UPDATE voice_sessions SET expires_at=? WHERE id=?',
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), session_id),
        )


def test_failed_dispatch_releases_capacity_without_resetting_ledger(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat_a = conversation(alice)
        bob = TestClient(app)
        chat_b = conversation(bob)
        reserved = reserve_ledger(alice)

        assert start(alice, chat_a).status_code == 201

        # Bob's failed slot must not stay counted as active.
        state['dispatch_fails'] = True
        failed = start(bob, chat_b)
        assert failed.status_code == 503
        assert failed.json()['detail']['code'] == 'VOICE_DISPATCH_FAILED'
        assert len(sessions_with_status(db_path, 'failed')) == 1

        state['dispatch_fails'] = False
        replacement = start(bob, chat_b)
        assert replacement.status_code == 201
        assert state['dispatched'][-1] == session_row(db_path, replacement.json()['id'])['room_name']
        assert session_row(db_path, replacement.json()['id'])['status'] == 'starting'

        # Two active sessions remain the hard cap; the failed one does not add a third.
        carol = TestClient(app)
        assert start(carol, conversation(carol)).status_code == 429
        assert reserve_ledger(alice) >= reserved


def test_expired_session_is_reaped_and_frees_capacity(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch, demo_session_budget=10)
    with alice:
        chat_a = conversation(alice)
        bob = TestClient(app)
        chat_b = conversation(bob)
        reserved = reserve_ledger(alice)

        first = start(alice, chat_a)
        assert first.status_code == 201
        assert start(bob, chat_b).status_code == 201

        carol = TestClient(app)
        chat_c = conversation(carol)
        assert start(carol, chat_c).status_code == 429

        expire(db_path, first.json()['id'])
        replacement = start(carol, chat_c)
        assert replacement.status_code == 201
        assert session_row(db_path, first.json()['id'])['status'] == 'ended'
        # Reaping an expired reservation is not room cleanup; no room delete was invented.
        assert state['deleted'] == []
        assert reserve_ledger(alice) >= reserved


def test_ending_twice_is_safe_and_cleans_room_once(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        chat = conversation(alice)
        session = start(alice, chat)
        assert session.status_code == 201
        session_id = session.json()['id']
        room = session_row(db_path, session_id)['room_name']

        assert alice.post(f'/v1/voice/sessions/{session_id}/end').status_code == 200
        second = alice.post(f'/v1/voice/sessions/{session_id}/end')
        assert second.status_code == 200
        assert second.json()['cleanup'] == 'already_ended'
        assert state['deleted'] == [room]


def test_room_cleanup_failure_is_actionable_and_retry_does_not_double_delete(tmp_path, monkeypatch):
    app, alice, state, db_path = build(tmp_path, monkeypatch)
    with alice:
        chat = conversation(alice)
        session = start(alice, chat)
        session_id = session.json()['id']
        state['delete_fails'] = True

        failed = alice.post(f'/v1/voice/sessions/{session_id}/end')
        assert failed.status_code == 503
        assert failed.json()['detail']['code'] == 'ROOM_CLEANUP_FAILED'
        # Capacity is released even when LiveKit room cleanup fails.
        assert session_row(db_path, session_id)['status'] == 'ended'

        state['delete_fails'] = False
        retried = alice.post(f'/v1/voice/sessions/{session_id}/end')
        assert retried.status_code == 200
        assert retried.json()['cleanup'] == 'already_ended'
        assert len(state['deleted']) == 1
