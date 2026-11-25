"""Voice-commanded UI controls: theme, speaking speed, captions and voice.

Four whole-utterance controls only. Theme is browser-local (the client applies
the hint and owns storage); speed, captions and voice are server-owned
preferences written through the one settings path. Near-misses must fall
through rather than partially apply.
"""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import Settings
from app.domain.commands import parse_exact_command, parse_voice_gender, parse_voice_selection
from app.domain.contracts import (
    AdjustSpeakingSpeed,
    SetCaptionsVisible,
    SetSpeakingSpeed,
    SetTheme,
)
from app.main import create_app
from app.services.voices import VoiceOption
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class Catalog:
    voices = (
        VoiceOption(id="approved", name="Approved Narrator", languages=("en",)),
        VoiceOption(id="woman-1", name="Aria", languages=("en",), gender="female"),
    )

    async def require(self, voice_id):
        for voice in self.voices:
            if voice.id == voice_id:
                return voice
        raise AssertionError("only approved voices are used in these tests")

    async def find_by_trait(self, trait, value, approved):
        for voice in self.voices:
            if voice.id in approved and getattr(voice, trait) == value:
                return voice
        return None

    async def find_by_name(self, name):
        wanted = " ".join(name.casefold().split())
        matches = [voice for voice in self.voices if " ".join(voice.name.casefold().split()) == wanted]
        return matches[0] if len(matches) == 1 else None


def app_for(tmp_path, name="voice-ui"):
    path = tmp_path / f"{name}.db"
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path}",
            approved_voice_ids="approved,woman-1",
            _env_file=None,
        ),
        voice_catalog=Catalog(),
    )
    return app, path


def chat(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def voice(client, conversation, text, *, request="r1", revision=0):
    return client.post(
        "/v1/actions",
        json={
            "conversation_id": conversation,
            "request_id": request,
            "expected_revision": revision,
            "source": "transcript",
            "transcript": text,
        },
    )


def counts(path):
    with closing(sqlite3.connect(path)) as db, db:
        return (
            db.execute("SELECT COUNT(*) FROM setting_revisions").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM context_operations").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM setting_write_requests").fetchone()[0],
        )


THEME_ROWS = [
    ("dark mode", "dark"),
    ("Dark mode.", "dark"),
    ("light mode", "light"),
    ("system theme", "system"),
    ("follow the system theme", "system"),
    ("use the system theme", "system"),
    ("switch to dark mode", "dark"),
    ("switch to light mode", "light"),
    ("switch to dark theme", "dark"),
    ("switch to light theme", "light"),
    ("switch to the dark theme", "dark"),
    ("switch to the light theme", "light"),
    ("turn on dark mode", "dark"),
    ("turn on light mode", "light"),
    ("use dark mode", "dark"),
    ("use light mode", "light"),
    ("use dark theme", "dark"),
    ("use light theme", "light"),
    ("use the dark theme", "dark"),
    ("use the light theme", "light"),
]


@pytest.mark.parametrize(("text", "theme"), THEME_ROWS)
def test_every_theme_phrase_maps_to_exactly_one_theme_action(text, theme):
    action = parse_exact_command(text)
    assert isinstance(action, SetTheme)
    assert action.theme == theme


SPEED_ROWS = [
    ("set speed to 1.2", 1.2),
    ("set the speed to 0.8", 0.8),
    ("set my speed to 1.0", 1.0),
    ("speak at 1.2x", 1.2),
    ("speak at 0.8x", 0.8),
    # Out-of-range requests are clamped to the interval the provider accepts.
    ("speak at 1.5x", 1.2),
    ("set speed to 0.5", 0.8),
]


@pytest.mark.parametrize(("text", "speed"), SPEED_ROWS)
def test_speed_value_phrases_map_to_one_setting(text, speed):
    action = parse_exact_command(text)
    assert isinstance(action, SetSpeakingSpeed)
    assert action.speed == speed


def test_existing_step_rows_still_use_relative_changes():
    assert parse_exact_command("speak slower") == AdjustSpeakingSpeed(delta=-0.1)
    assert parse_exact_command("speak faster") == AdjustSpeakingSpeed(delta=0.1)


@pytest.mark.parametrize(
    "text",
    [
        "dark mode and hide captions",
        "don't switch to dark mode",
        "darkmode",
        "switch to dark mode and light mode",
        "set speed to fast",
        "set speed to 1.05",
        "speak at 1.5x and hide captions",
        "make it faster",
    ],
)
def test_near_misses_are_not_commands_and_never_apply_partially(text):
    assert parse_exact_command(text) is None
    assert parse_voice_selection(text) is None


@pytest.mark.parametrize(
    ("text", "visible"),
    [
        ("turn captions off", False),
        ("turn captions on", True),
        ("captions off", False),
        ("captions on", True),
        ("hide captions", False),
        ("turn on the captions", True),
    ],
)
def test_everyday_caption_forms_map_to_one_caption_action(text, visible):
    action = parse_exact_command(text)
    assert isinstance(action, SetCaptionsVisible)
    assert action.visible is visible


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("use the approved narrator voice", "approved narrator"),
        ("use the Approved Narrator voice.", "Approved Narrator"),
    ],
)
def test_daily_voice_phrase_is_an_argument_bearing_selection(text, name):
    assert parse_voice_selection(text) == name
    assert parse_exact_command(text) is None


def test_theme_result_carries_the_hint_without_a_settings_write(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, "Dark mode.")
        assert response.status_code == 200
        body = response.json()
        assert body["handled"] is True
        result = body["result"]
        assert result["kind"] == "executed"
        assert result["action"] == "SetTheme"
        assert result["theme"] == "dark"
        assert result["settings"] is None
        assert result["changed"] is True
        assert result["spoken"] == "Dark mode on."
        # The server keeps no theme: settings stay at revision 0 and untouched.
        assert client.get("/v1/settings").json()["revision"] == 0
        assert counts(path) == (0, 0, 0)


def test_theme_result_replays_for_a_durable_request_id(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        first = voice(client, conversation, "Follow the system theme.", request="theme-1")
        duplicate = voice(client, conversation, "  follow   the system theme.  ", request="theme-1")
        assert duplicate.json() == first.json()
        assert first.json()["result"]["theme"] == "system"
        assert first.json()["result"]["spoken"] == "Following the system theme."
        assert counts(path) == (0, 0, 0)


def test_theme_cannot_be_persisted_as_a_server_setting(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        client.post("/v1/demo-session")
        response = client.post(
            "/v1/settings/actions",
            json={"request_id": "theme-1", "expected_revision": 0, "action": {"kind": "SetTheme", "theme": "dark"}},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_SETTING_ACTION"
        assert counts(path) == (0, 0, 0)


def test_speed_value_by_voice_commits_and_is_heard_on_the_next_reply(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        result = voice(client, conversation, "Set the speed to 1.2.").json()["result"]
        assert result["kind"] == "executed"
        assert result["action"] == "SetSpeakingSpeed"
        assert result["settings"]["speaking_speed"] == 1.2
        assert result["settings"]["revision"] == 1
        assert result["spoken"] == "Speaking speed set to 1.2 for the next reply."
        assert client.get("/v1/settings").json()["speaking_speed"] == 1.2
        assert counts(path) == (1, 0, 1)

        duplicate = voice(client, conversation, " set  the speed to 1.2 ", revision=0).json()
        assert duplicate == {"handled": True, "result": result}
        assert counts(path) == (1, 0, 1)


def test_unchanged_speed_is_reported_honestly(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        result = voice(client, conversation, "Set speed to 1.0.").json()["result"]
        assert result["kind"] == "unchanged"
        assert result["changed"] is False
        assert result["spoken"] == "Speaking speed is already 1.0."


def test_captions_and_voice_keep_the_one_settings_path(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        off = voice(client, conversation, "Turn captions off.").json()["result"]
        assert off["kind"] == "executed"
        assert off["action"] == "SetCaptionsVisible"
        assert off["settings"]["captions_visible"] is False
        assert off["spoken"] == "Captions hidden."

        on = voice(client, conversation, "Captions on.", request="r2", revision=1).json()["result"]
        assert on["settings"]["captions_visible"] is True
        assert on["spoken"] == "Captions shown."

        chosen = voice(client, conversation, "Use the Approved Narrator voice.", request="r3", revision=2).json()[
            "result"
        ]
        assert chosen["action"] == "SelectApprovedVoice"
        assert chosen["settings"]["approved_voice_id"] == "approved"
        assert chosen["spoken"] == "Voice set to Approved Narrator for the next call."
        assert counts(path) == (3, 0, 3)


def test_compound_theme_utterance_falls_through_without_effects(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        assert voice(client, conversation, "Dark mode and hide captions.").json() == {"handled": False}
        assert counts(path) == (0, 0, 0)
        assert client.get("/v1/settings").json() == {
            "speaking_speed": 1.0,
            "captions_visible": True,
            "approved_voice_id": None,
            "response_preference": "automatic",
            "memory_mode": "auto",
            "revision": 0,
        }


def test_checked_in_schema_exposes_the_theme_contract():
    spec = json.loads((ROOT / "packages/contracts/openapi.json").read_text())
    schemas = spec["components"]["schemas"]
    assert schemas["SetTheme"]["properties"]["theme"]["enum"] == ["light", "dark", "system"]
    assert any(ref["$ref"].endswith("/SetTheme") for ref in schemas["Action"]["oneOf"])
    theme = schemas["CommandResult"]["properties"]["theme"]
    assert {"light", "dark", "system"} <= set(theme["anyOf"][0]["enum"])


def _voice_session(client, file, conversation):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) VALUES (?,?,?,?,?,?,?)",
            (
                "session",
                conversation,
                "owner",
                "room",
                "active",
                (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                json.dumps({}),
            ),
        )
    return {"x-agent-secret": "test-service-secret", "x-session-id": "session", "x-room-name": "room"}


def test_worker_delivers_the_theme_hint_on_the_command_channel(tmp_path):
    file = tmp_path / "worker-theme.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}", agent_service_secret="test-service-secret", _env_file=None
    )
    with TestClient(create_app(settings, voice_catalog=Catalog())) as client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        turn_id = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "source-1", "text": "Light mode."}
        ).json()["turn_id"]

        command = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id}).json()["command"]
        assert command["action"] == "SetTheme"
        assert command["theme"] == "light"
        assert command["settings"] is None
        assert command["spoken"] == "Light mode on."


@pytest.mark.parametrize(
    ("text", "gender"),
    [
        ("change the voice to a man", "male"),
        ("Change the voice to a woman.", "female"),
        ("change my voice to a female", "female"),
        ("switch to a male voice", "male"),
        ("use a female voice", "female"),
        ("change to a woman voice", "female"),
    ],
)
def test_gender_voice_phrases(text, gender):
    assert parse_voice_gender(text) == gender
    assert parse_exact_command(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "change the voice to a manly voice",
        "use the male voice and hide captions",
        "use a woman",
        "a female voice",
        "use a robot voice",
        "don't use a male voice",
    ],
)
def test_gender_near_misses_return_none(text):
    assert parse_voice_gender(text) is None


def test_gender_voice_command_selects_an_approved_voice_and_replays(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        first = voice(client, conversation, "Change the voice to a woman.").json()["result"]
        assert first["kind"] == "executed"
        assert first["settings"]["approved_voice_id"] == "woman-1"
        assert first["spoken"] == "Voice set to Aria for the next call."
        assert (
            voice(client, conversation, "Change the voice to a woman.").json()["result"]["request_id"]
            == (first["request_id"])
        )
        assert counts(path)[0] == 1


def test_gender_voice_command_refuses_honestly_without_a_match(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        result = voice(client, conversation, "Use a male voice.").json()["result"]
        assert result["kind"] == "unsupported"
        assert result["detail"] == "VOICE_GENDER_NOT_FOUND"
        assert result["settings"] is None
        assert "male" in result["spoken"]
        assert counts(path) == (0, 0, 0)
