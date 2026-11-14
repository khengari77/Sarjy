"""The finalized-utterance seam: deterministic commands, parity and truthfulness."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.main import create_app
from app.services.voices import VoiceCatalogError, VoiceOption
from fastapi.testclient import TestClient


class Catalog:
    voices = (VoiceOption(id="approved", name="Approved Narrator", languages=("en",)),)

    async def require(self, voice_id):
        for voice in self.voices:
            if voice.id == voice_id:
                return voice
        raise VoiceCatalogError("VOICE_UNAVAILABLE", 422)


def app_for(tmp_path, name="commands"):
    path = tmp_path / f"{name}.db"
    app = create_app(
        Settings(database_url=f"sqlite+aiosqlite:///{path}", _env_file=None),
        voice_catalog=Catalog(),
    )
    return app, path


def chat(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def submit(client, conversation, *, request="r1", revision=0, source, **extra):
    return client.post(
        "/v1/actions",
        json={
            "conversation_id": conversation,
            "request_id": request,
            "expected_revision": revision,
            "source": source,
            **extra,
        },
    )


def voice(client, conversation, text, *, request="r1", revision=0):
    return submit(client, conversation, request=request, revision=revision, source="transcript", transcript=text)


def gui(client, conversation, action, *, request="r1", revision=0):
    return submit(client, conversation, request=request, revision=revision, source="ui", action=action)


def counts(path):
    with closing(sqlite3.connect(path)) as db, db:
        return (
            db.execute("SELECT COUNT(*) FROM setting_revisions").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM context_operations").fetchone()[0],
        )


ROWS = [
    ("Speak slower.", {"kind": "AdjustSpeakingSpeed", "delta": -0.1}, "executed"),
    ("Speak faster.", {"kind": "AdjustSpeakingSpeed", "delta": 0.1}, "executed"),
    ("Hide captions.", {"kind": "SetCaptionsVisible", "visible": False}, "executed"),
    # Captions start visible, so the exact command reports an unchanged state
    # instead of claiming a change that did not happen.
    ("Show captions.", {"kind": "SetCaptionsVisible", "visible": True}, "unchanged"),
    ("Show my previous conversations.", {"kind": "ShowConversationHistory"}, "executed"),
    ("Open my previous conversations.", {"kind": "ShowConversationHistory"}, "executed"),
    ("Open the memory explorer.", {"kind": "OpenMemoryExplorer"}, "executed"),
]
SETTING_KINDS = {"AdjustSpeakingSpeed", "SetCaptionsVisible"}


@pytest.mark.parametrize(("text", "action", "expected_kind"), ROWS)
def test_voice_and_gui_fixtures_converge_on_one_state_and_handler_trace(tmp_path, text, action, expected_kind):
    spoken_app, spoken_path = app_for(tmp_path, "spoken")
    with TestClient(spoken_app) as client:
        conversation = chat(client)
        response = voice(client, conversation, text)
        assert response.status_code == 200
        spoken = response.json()
        spoken_settings = client.get("/v1/settings").json()
        spoken_counts = counts(spoken_path)

    gui_app, gui_path = app_for(tmp_path, "gui")
    with TestClient(gui_app) as client:
        conversation = chat(client)
        response = gui(client, conversation, action)
        assert response.status_code == 200
        manual = response.json()
        gui_settings = client.get("/v1/settings").json()
        gui_counts = counts(gui_path)

    assert spoken["handled"] is True and manual["handled"] is True
    assert spoken["result"]["kind"] == expected_kind
    assert spoken["result"]["changed"] is (expected_kind == "executed")
    assert spoken_settings == gui_settings
    assert spoken_counts == gui_counts
    assert spoken["result"]["action"] == action["kind"]
    assert spoken["result"]["spoken"]
    if action["kind"] in SETTING_KINDS:
        assert spoken["result"]["settings"] == gui_settings
    else:
        assert spoken["result"]["navigation"] == manual["result"]["navigation"]


def test_duplicate_finalized_transcript_applies_one_effect(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        first = voice(client, conversation, "Speak slower.")
        # A replayed final transcript carries the same source request id.
        duplicate = voice(client, conversation, " Speak   slower. ")
        assert duplicate.json() == first.json()
        assert client.get("/v1/settings").json()["speaking_speed"] == 0.9
        (revisions, operations) = counts(path)
        assert (revisions, operations) == (1, 1)

        # A genuinely new utterance is a new command and applies again.
        second = voice(client, conversation, "Speak slower.", request="r2", revision=1)
        assert second.json()["result"]["settings"]["speaking_speed"] == 0.8


@pytest.mark.parametrize(
    "text",
    [
        "Use the British female voice.",
        "Show my memories about Paris.",
        "Forget the Paris trip but keep the budget.",
        "Restore the memories I excluded.",
        "Give shorter answers from now on.",
        "Clear context.",
    ],
)
def test_unwired_parity_rows_are_unsupported_and_mutate_nothing(tmp_path, text):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, text)
        assert response.status_code == 200
        body = response.json()
        assert body["handled"] is True
        assert body["result"]["kind"] == "unsupported"
        assert body["result"]["action"] is None
        assert body["result"]["settings"] is None
        assert body["result"]["changed"] is False
        assert body["result"]["spoken"]
        assert client.get("/v1/settings").json()["revision"] == 0
        assert counts(path) == (0, 0)


@pytest.mark.parametrize(
    "text",
    [
        "What is the weather in Tripoli?",
        "Do not hide captions.",
        "Hide captions and speak slower.",
        '"Speak slower."',
        "Please explain why you speak slower.",
    ],
)
def test_non_commands_fall_through_without_effects(tmp_path, text):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, text)
        assert response.status_code == 200
        assert response.json() == {"handled": False}
        assert counts(path) == (0, 0)


def test_stale_revision_reports_failure_without_claiming_success(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, "Hide captions.", revision=9)
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["kind"] == "failed"
        assert result["action"] == "SetCaptionsVisible"
        assert "couldn't apply" in result["spoken"]
        assert client.get("/v1/settings").json()["captions_visible"] is True


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


def test_worker_command_uses_the_stored_final_turn_once(tmp_path):
    file = tmp_path / "worker.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}", agent_service_secret="test-service-secret", _env_file=None
    )
    with TestClient(create_app(settings, voice_catalog=Catalog())) as client:
        owner = client.post("/v1/demo-session").json()["id"]
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        started = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "source-1", "text": "Hide captions."}
        )
        assert started.status_code == 200
        turn_id = started.json()["turn_id"]

        first = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id})
        assert first.status_code == 200
        command = first.json()["command"]
        assert command["kind"] == "executed" and command["action"] == "SetCaptionsVisible"
        assert command["spoken"] == "Captions hidden."
        duplicate = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id})
        assert duplicate.json() == first.json()
        assert client.get("/v1/settings").json()["captions_visible"] is False
        assert counts(file) == (1, 1)
        assert owner

        ordinary = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "source-2", "text": "Tell me a story."}
        )
        assert client.post(
            "/internal/agent/command", headers=headers, json={"turn_id": ordinary.json()["turn_id"]}
        ).json() == {"command": None}
        assert client.post("/internal/agent/command", headers=headers, json={"turn_id": "missing"}).status_code == 404


def test_worker_command_requires_the_owning_session(tmp_path):
    file = tmp_path / "worker.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}", agent_service_secret="test-service-secret", _env_file=None
    )
    with TestClient(create_app(settings, voice_catalog=Catalog())) as client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        headers = _voice_session(client, file, conversation)
        turn_id = client.post(
            "/internal/agent/turn/start", headers=headers, json={"request_id": "source-1", "text": "Hide captions."}
        ).json()["turn_id"]
        assert client.post("/internal/agent/command", json={"turn_id": turn_id}).status_code == 401
        assert (
            client.post(
                "/internal/agent/command", headers=headers | {"x-room-name": "elsewhere"}, json={"turn_id": turn_id}
            ).status_code
            == 403
        )
