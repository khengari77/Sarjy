import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from app.config import Settings
from app.domain.voices import parse_voice_ids
from app.main import create_app
from app.services.voices import (
    VoiceCatalogError,
    VoiceOption,
    saved_voice_outcome,
    spoken_voice_language,
)
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st


@given(st.lists(st.from_regex(r"[A-Za-z0-9_-]{1,20}", fullmatch=True), max_size=10))
def test_parse_voice_ids_round_trips_valid_identifiers(identifiers):
    assert parse_voice_ids(",".join(identifiers)) == tuple(dict.fromkeys(identifiers))


@pytest.mark.parametrize("raw", ["bad id", "ok,has space", "ok,$bad"])
def test_parse_voice_ids_rejects_malformed_identifiers(raw):
    with pytest.raises(ValueError, match="VOICE_IDS_INVALID"):
        parse_voice_ids(raw)


def test_settings_reject_a_malformed_approved_voice_list():
    with pytest.raises(ValueError, match="VOICE_IDS_INVALID"):
        Settings(approved_voice_ids="good,bad id", _env_file=None)


def test_voice_configuration_is_not_a_boot_requirement():
    # Voice is a per-session user setting (#148): a missing voice fails the session, not startup.
    assert Settings(_env_file=None).approved_voice_ids == ""
    assert Settings(default_voice_id="aura-2-zeus-en", _env_file=None).default_voice_id == "aura-2-zeus-en"


def configured(tmp_path):
    return Settings(
        default_llm_provider="openai_direct",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/voice.db",
        livekit_url="wss://test.invalid",
        livekit_api_key="test-key",
        livekit_api_secret="test-secret-with-enough-characters",
        agent_service_secret="worker-secret",
        deepgram_api_key="stt-test",
        openai_api_key="llm-test",
        approved_voice_ids="aura-2-thalia-en,aura-2-apollo-en,aura-2-missing-en",
        _env_file=None,
    )


def test_authenticated_catalog_search_and_safe_response(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        assert client.get("/v1/voices").status_code == 401
        client.post("/v1/demo-session")
        response = client.get("/v1/voices?search=Thalia")
        assert response.json() == {
            "voices": [
                {
                    "id": "aura-2-thalia-en",
                    "name": "Thalia",
                    "languages": ["en"],
                    "gender": "female",
                    "accent": "american",
                    "age": "adult",
                    "description": "clear, confident, energetic, enthusiastic",
                }
            ],
            "next_page_token": None,
        }


def test_unavailable_voice_never_reserves_or_dispatches_a_call(tmp_path):
    settings = configured(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        for selection, status, code in [
            ("aura-2-missing-en", 422, "VOICE_UNAVAILABLE"),
            ("aura-2-zeus-en", 422, "VOICE_NOT_APPROVED"),
        ]:
            response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "voice_id": selection})
            assert response.status_code == status
            assert response.json()["detail"]["code"] == code
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            assert db.execute("SELECT COUNT(*) FROM voice_sessions").fetchone()[0] == 0


def configured_with_default(tmp_path, **overrides):
    return configured(tmp_path).model_copy(
        update={
            "approved_voice_ids": "aura-2-apollo-en",
            "default_voice_id": "aura-2-thalia-en",
            "default_voice_language": "en",
            **overrides,
        }
    )


def test_fresh_browser_starts_with_the_catalog_validated_default(tmp_path, livekit_control_plane):
    with TestClient(create_app(configured_with_default(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert response.status_code == 201
        body = response.json()
        assert body["voice"] == {
            "id": "aura-2-thalia-en",
            "name": "Thalia",
            "languages": ["en"],
            "gender": "female",
            "accent": "american",
            "age": "adult",
            "description": "clear, confident, energetic, enthusiastic",
        }
        assert (body["voice_source"], body["voice_language"]) == ("default", "en")
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            pipeline = json.loads(
                db.execute("SELECT pipeline FROM voice_sessions WHERE id=?", (body["id"],)).fetchone()[0]
            )
        assert pipeline["tts"] == {
            "provider": "deepgram_direct",
            "model": "aura-2",
            "voice_id": "aura-2-thalia-en",
            "language": "en",
        }
        assert pipeline["stt"]["language"] == "en"


def test_saved_server_setting_outranks_the_default_and_is_catalog_validated(tmp_path, livekit_control_plane):
    settings = configured_with_default(
        tmp_path, approved_voice_ids="aura-2-apollo-en,aura-2-thalia-en", default_voice_id="aura-2-apollo-en"
    )
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        rejected = client.post(
            "/v1/actions",
            json={
                "conversation_id": chat,
                "request_id": "unapproved",
                "expected_revision": 0,
                "source": "ui",
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-zeus-en"},
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "VOICE_NOT_APPROVED"
        saved = client.post(
            "/v1/actions",
            json={
                "conversation_id": chat,
                "request_id": "approved",
                "expected_revision": 0,
                "source": "ui",
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["result"]["settings"]["approved_voice_id"] == "aura-2-thalia-en"
        response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert response.status_code == 201
        body = response.json()
        assert (body["voice"]["id"], body["voice_source"], body["voice_language"]) == (
            "aura-2-thalia-en",
            "saved",
            "en",
        )


def test_owner_scoped_settings_save_a_voice_without_creating_a_conversation(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        assert client.get("/v1/conversations").json() == []
        response = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "first-visit",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["settings"]["approved_voice_id"] == "aura-2-thalia-en"
        assert response.json()["settings"]["revision"] == 1
        assert client.get("/v1/settings").json()["approved_voice_id"] == "aura-2-thalia-en"
        # Persisting a preference must not add a chat row.
        assert client.get("/v1/conversations").json() == []


def test_owner_scoped_settings_enforce_approval_revision_and_action_kind(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        unapproved = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "unapproved",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-zeus-en"},
            },
        )
        assert unapproved.status_code == 422
        assert unapproved.json()["detail"]["code"] == "VOICE_NOT_APPROVED"
        saved = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "approved",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-apollo-en"},
            },
        )
        assert saved.status_code == 200, saved.text
        stale = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "stale",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "SETTINGS_REVISION_CONFLICT"
        not_a_setting = client.post(
            "/v1/settings/actions",
            json={"request_id": "navigation", "expected_revision": 1, "action": {"kind": "OpenMemoryExplorer"}},
        )
        assert not_a_setting.status_code == 422
        assert not_a_setting.json()["detail"]["code"] == "INVALID_SETTING_ACTION"


def test_owner_scoped_settings_replay_a_duplicate_request(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        body = {
            "request_id": "lost-response",
            "expected_revision": 0,
            "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
        }
        first = client.post("/v1/settings/actions", json=body)
        assert first.status_code == 200, first.text
        # The client retries after losing the response: same key, same old revision.
        retry = client.post("/v1/settings/actions", json=body)
        assert retry.status_code == 200, retry.text
        assert retry.json() == first.json()
        assert retry.json()["settings"]["revision"] == 1
        assert client.get("/v1/settings").json()["approved_voice_id"] == "aura-2-thalia-en"
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            assert db.execute("SELECT COUNT(*) FROM setting_revisions").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM setting_write_requests").fetchone()[0] == 1


def test_owner_scoped_settings_reject_a_reused_request_id_for_a_different_action(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        first = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "reused",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-apollo-en"},
            },
        )
        assert first.status_code == 200, first.text
        conflict = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "reused",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert client.get("/v1/settings").json()["approved_voice_id"] == "aura-2-apollo-en"


def test_conversation_voice_action_uses_the_single_owner_scoped_writer(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        through_conversation = client.post(
            "/v1/actions",
            json={
                "conversation_id": chat,
                "request_id": "from-conversation",
                "expected_revision": 0,
                "source": "ui",
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
            },
        )
        assert through_conversation.status_code == 200, through_conversation.text
        conversation_result = through_conversation.json()["result"]
        # The same action through the owner-scoped endpoint sees the same stored
        # setting: already applied, so no further revision bump.
        through_settings = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "from-settings",
                "expected_revision": 1,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
            },
        )
        assert through_settings.status_code == 200, through_settings.text
        assert through_settings.json()["settings"] == conversation_result["settings"]
        assert through_settings.json()["changed"] is False
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            # One writer for voice: no conversation-scoped operation is recorded,
            # and a single revision covers both requests.
            assert db.execute("SELECT COUNT(*) FROM context_operations").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM setting_revisions").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM setting_write_requests").fetchone()[0] == 2


def test_saved_voice_language_is_derived_from_the_catalog():
    arabic = VoiceOption(id="aura-2-thalia-en", name="Arabic", languages=("ar",))
    undeclared = VoiceOption(id="aura-2-apollo-en", name="Other")
    # A saved Arabic voice stays Arabic even when the browser requests English.
    assert spoken_voice_language("saved", "en", arabic, "en") == "ar"
    assert spoken_voice_language("saved", "ar", arabic, "en") == "ar"
    # A silent catalog uses the configured default language, never the request:
    # the saved voice is a server-owned choice, the request may be another language.
    assert spoken_voice_language("saved", "en", undeclared, "ar") == "ar"
    assert spoken_voice_language("saved", "ar", undeclared, "en") == "en"
    # Explicit requests and the configured default keep their own rules.
    assert spoken_voice_language("requested", "en", arabic, "ar") == "en"
    assert spoken_voice_language("default", "en", arabic, "ar") == "ar"


def test_saved_voice_uses_its_catalog_language(tmp_path, livekit_control_plane):
    settings = configured_with_default(tmp_path, approved_voice_ids="aura-2-apollo-en,aura-2-thalia-en")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        saved = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "save-other",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-apollo-en"},
            },
        )
        assert saved.status_code == 200, saved.text
        response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert response.status_code == 201, response.text
        body = response.json()
        # The saved voice's catalog language wins over the request.
        assert (body["voice"]["id"], body["voice_source"], body["voice_language"]) == (
            "aura-2-apollo-en",
            "saved",
            "en",
        )


def test_clearing_a_saved_voice_restores_the_server_default(tmp_path, livekit_control_plane):
    settings = configured_with_default(tmp_path, approved_voice_ids="aura-2-apollo-en,aura-2-thalia-en")
    with TestClient(create_app(settings)) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        saved = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "save",
                "expected_revision": 0,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-apollo-en"},
            },
        )
        assert saved.status_code == 200, saved.text
        cleared = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "clear",
                "expected_revision": 1,
                "action": {"kind": "SelectApprovedVoice", "voice_id": None},
            },
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["settings"]["approved_voice_id"] is None
        response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert response.status_code == 201, response.text
        body = response.json()
        assert (body["voice"]["id"], body["voice_source"]) == ("aura-2-thalia-en", "default")


def test_saved_voice_removed_from_allow_list_falls_back_to_the_default(tmp_path, livekit_control_plane):
    settings = configured_with_default(tmp_path, approved_voice_ids="aura-2-thalia-en")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        # Simulate a saved id that was approved at save time and has since been
        # removed from the allow-list.
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            db.execute(
                "INSERT INTO user_settings"
                " (owner_id, speaking_speed, captions_visible, approved_voice_id, response_preference, revision, updated_at)"
                " VALUES (?, 1.0, 1, 'aura-2-apollo-en', 'automatic', 1, '2026-01-01T00:00:00+00:00')",
                (owner,),
            )
        response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["voice"]["id"] == "aura-2-thalia-en"
        assert body["voice_source"] == "default"
        assert body["saved_voice_unavailable"] == "removed"
        # The dead preference is cleared, so the picker cannot keep showing it.
        settings_after = client.get("/v1/settings").json()
        assert settings_after["approved_voice_id"] is None
        assert settings_after["revision"] == 2
        client.post(f"/v1/voice/sessions/{body['id']}/end")
        second = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert second.status_code == 201, second.text
        assert second.json()["saved_voice_unavailable"] is None


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        ("VOICE_NOT_APPROVED", "removed"),
        ("VOICE_UNAVAILABLE", "removed"),
        ("VOICE_CATALOG_UNAVAILABLE", "transient"),
        ("VOICE_CATALOG_ACCESS_DENIED", "transient"),
        ("VOICE_PROVIDER_NOT_CONFIGURED", "transient"),
    ],
)
def test_only_definitive_catalog_codes_count_as_removed(code, outcome):
    assert saved_voice_outcome(code) == outcome


def _saved_setting(tmp_path, owner, voice_id):
    with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
        db.execute(
            "INSERT INTO user_settings"
            " (owner_id, speaking_speed, captions_visible, approved_voice_id, response_preference, revision, updated_at)"
            " VALUES (?, 1.0, 1, ?, 'automatic', 1, '2026-01-01T00:00:00+00:00')",
            (owner, voice_id),
        )


def test_provider_not_configured_keeps_the_saved_preference(tmp_path, livekit_control_plane):
    class SelectiveCatalog:
        async def require(self, voice_id):
            if voice_id == "aura-2-missing-en":
                raise VoiceCatalogError("VOICE_PROVIDER_NOT_CONFIGURED", 503)
            return VoiceOption(id=voice_id, name="Default voice", languages=("en",))

    settings = configured_with_default(tmp_path, approved_voice_ids="aura-2-missing-en,aura-2-thalia-en")
    with TestClient(create_app(settings, voice_catalog=SelectiveCatalog())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        _saved_setting(tmp_path, owner, "aura-2-missing-en")
        response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert response.status_code == 201, response.text
        assert response.json()["voice"]["id"] == "aura-2-thalia-en"
        assert response.json()["saved_voice_unavailable"] == "transient"
        assert client.get("/v1/settings").json()["approved_voice_id"] == "aura-2-missing-en"


def test_voice_unavailable_clears_the_preference(tmp_path, livekit_control_plane):
    settings = configured_with_default(tmp_path, approved_voice_ids="aura-2-missing-en,aura-2-thalia-en")
    with TestClient(create_app(settings)) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        chat = client.post("/v1/conversations").json()["id"]
        _saved_setting(tmp_path, owner, "aura-2-missing-en")
        response = client.post("/v1/voice/sessions", json={"conversation_id": chat, "language": "en"})
        assert response.status_code == 201, response.text
        assert response.json()["voice"]["id"] == "aura-2-thalia-en"
        assert response.json()["saved_voice_unavailable"] == "removed"
        assert client.get("/v1/settings").json()["approved_voice_id"] is None


@pytest.fixture
def livekit_control_plane(monkeypatch):
    # Replace only the external LiveKit control-plane boundary. State and auth are real.
    from livekit import api

    class ControlPlane:
        def __init__(self, *args, **kwargs):
            self.agent_dispatch = SimpleNamespace(create_dispatch=self.dispatch)
            self.room = SimpleNamespace(delete_room=self.delete)

        async def dispatch(self, request):
            return None

        async def delete(self, request):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(api, "LiveKitAPI", ControlPlane)


def test_selected_voice_is_frozen_per_call_and_reaches_snapshot(tmp_path, livekit_control_plane):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]

        def start(voice):
            return client.post("/v1/voice/sessions", json={"conversation_id": chat, "voice_id": voice})

        first = start("aura-2-thalia-en")
        assert first.status_code == 201
        session_id = first.json()["id"]
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            room, pipeline = db.execute(
                "SELECT room_name,pipeline FROM voice_sessions WHERE id=?", (session_id,)
            ).fetchone()
        assert json.loads(pipeline)["tts"]["voice_id"] == "aura-2-thalia-en"
        assert start("aura-2-apollo-en").status_code == 429
        headers = {"x-agent-secret": "worker-secret", "x-session-id": session_id, "x-room-name": room}
        snapshot = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "utterance", "text": "Hello"}
        ).json()["context"]
        assert snapshot["pipeline_config"]["tts"]["voice_id"] == "aura-2-thalia-en"
        assert client.post(f"/v1/voice/sessions/{session_id}/end").status_code == 200
        assert start("aura-2-apollo-en").status_code == 201


def test_start_voice_resolves_tier_from_preference_and_bootstrap_carries_guidance(tmp_path, livekit_control_plane):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]

        def start(**extra):
            response = client.post(
                "/v1/voice/sessions", json={"conversation_id": chat, "voice_id": "aura-2-thalia-en", **extra}
            )
            assert response.status_code == 201
            session = response.json()
            with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
                room, pipeline = db.execute(
                    "SELECT room_name,pipeline FROM voice_sessions WHERE id=?", (session["id"],)
                ).fetchone()
            session.update(room=room, pipeline=json.loads(pipeline))
            return session

        def bootstrap(session):
            headers = {"x-agent-secret": "worker-secret", "x-session-id": session["id"], "x-room-name": session["room"]}
            return client.post("/internal/agent/bootstrap", headers=headers).json()

        # An explicit current-turn request overrides the saved preference.
        brief = start(response_tier=5, response_preference="brief")
        assert (brief["pipeline"]["response_tier"], brief["pipeline"]["max_output_tokens"]) == (5, 1024)
        assert client.post(f"/v1/voice/sessions/{brief['id']}/end").status_code == 200

        detailed = start(response_tier=4, response_preference="detailed")
        assert (detailed["pipeline"]["response_tier"], detailed["pipeline"]["max_output_tokens"]) == (4, 512)
        instructions = bootstrap(detailed)["instructions"]
        assert "about 180 words or fewer" in instructions
        assert "512" not in instructions and "token" not in instructions.lower()


def test_legacy_bootstrap_and_snapshot_share_effective_configuration(tmp_path, livekit_control_plane):
    with TestClient(create_app(configured(tmp_path))) as client:
        client.post("/v1/demo-session")
        chat = client.post("/v1/conversations").json()["id"]
        session = client.post(
            "/v1/voice/sessions",
            json={
                "conversation_id": chat,
                "voice_id": "aura-2-thalia-en",
                "response_tier": 5,
                "response_preference": "brief",
            },
        ).json()
        with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
            room, raw = db.execute(
                "SELECT room_name,pipeline FROM voice_sessions WHERE id=?", (session["id"],)
            ).fetchone()
        headers = {"x-agent-secret": "worker-secret", "x-session-id": session["id"], "x-room-name": room}
        for legacy in [False, True]:
            if legacy:
                source = json.loads(raw)
                source.pop("response_tier")
                source.pop("response_preference")
                source["max_output_tokens"] = 96
                source["revision"] = 1
                with closing(sqlite3.connect(tmp_path / "voice.db")) as db, db:
                    db.execute("UPDATE voice_sessions SET pipeline=? WHERE id=?", (json.dumps(source), session["id"]))
            bootstrap = client.post("/internal/agent/bootstrap", headers=headers).json()
            body = {"request_id": str(legacy), "text": "Hello"}
            first = client.post("/internal/agent/turn/start", headers=headers, json=body).json()
            repeat = client.post("/internal/agent/turn/start", headers=headers, json=body).json()
            assert first == repeat
            assert first["context"]["pipeline_config"] == bootstrap["pipeline"]
            assert bootstrap["pipeline"]["max_output_tokens"] == (128 if legacy else 1024)
            assert first["context"]["messages"][0]["content"] == bootstrap["instructions"]


def test_committed_settings_receipt_replays_during_catalog_outage(tmp_path):
    class FlakyCatalog:
        failing = False

        async def require(self, voice_id):
            if self.failing:
                raise VoiceCatalogError("VOICE_CATALOG_UNAVAILABLE", 503)
            return VoiceOption(id=voice_id, name="Voice")

    catalog = FlakyCatalog()
    with TestClient(create_app(configured(tmp_path), voice_catalog=catalog)) as client:
        client.post("/v1/demo-session")
        body = {
            "request_id": "committed-before-outage",
            "expected_revision": 0,
            "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-apollo-en"},
        }
        first = client.post("/v1/settings/actions", json=body)
        assert first.status_code == 200, first.text
        catalog.failing = True
        replay = client.post("/v1/settings/actions", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        # An unseen key still needs the provider and fails honestly.
        unseen = client.post(
            "/v1/settings/actions",
            json={
                "request_id": "new-during-outage",
                "expected_revision": 1,
                "action": {"kind": "SelectApprovedVoice", "voice_id": "aura-2-thalia-en"},
            },
        )
        assert unseen.status_code == 503
        assert unseen.json()["detail"]["code"] == "VOICE_CATALOG_UNAVAILABLE"
