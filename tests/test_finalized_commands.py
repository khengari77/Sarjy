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

    async def find_by_name(self, name):
        wanted = " ".join(name.casefold().split())
        matches = [voice for voice in self.voices if " ".join(voice.name.casefold().split()) == wanted]
        return matches[0] if len(matches) == 1 else None


def app_for(tmp_path, name="commands"):
    path = tmp_path / f"{name}.db"
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path}",
            approved_voice_ids="approved",
            _env_file=None,
        ),
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
            db.execute("SELECT COUNT(*) FROM setting_write_requests").fetchone()[0],
        )


def handler_trace(path):
    """The typed action each committed dispatch recorded, in commit order."""
    with closing(sqlite3.connect(path)) as db, db:
        payloads = [row[0] for row in db.execute("SELECT payload FROM context_operations ORDER BY created_at")]
    return [json.loads(payload).get("result", {}).get("kind") for payload in payloads]


ROWS = [
    ("Show my previous conversations.", {"kind": "ShowConversationHistory"}, "executed"),
    ("Open my previous conversations.", {"kind": "ShowConversationHistory"}, "executed"),
    ("Open the memory explorer.", {"kind": "OpenMemoryExplorer"}, "executed"),
]


@pytest.mark.parametrize(("text", "action", "expected_kind"), ROWS)
def test_navigation_voice_and_gui_fixtures_converge_on_one_state_and_handler_trace(
    tmp_path, text, action, expected_kind
):
    spoken_app, spoken_path = app_for(tmp_path, "spoken")
    with TestClient(spoken_app) as client:
        conversation = chat(client)
        response = voice(client, conversation, text)
        assert response.status_code == 200
        spoken = response.json()
        spoken_settings = client.get("/v1/settings").json()
        spoken_counts = counts(spoken_path)
        spoken_trace = handler_trace(spoken_path)

    gui_app, gui_path = app_for(tmp_path, "gui")
    with TestClient(gui_app) as client:
        conversation = chat(client)
        response = gui(client, conversation, action)
        assert response.status_code == 200
        manual = response.json()
        gui_settings = client.get("/v1/settings").json()
        gui_counts = counts(gui_path)
        gui_trace = handler_trace(gui_path)

    assert spoken["handled"] is True and manual["handled"] is True
    assert spoken["result"]["kind"] == expected_kind
    assert spoken["result"]["changed"] is True
    assert spoken_settings == gui_settings
    assert spoken_counts == gui_counts
    assert spoken_trace == gui_trace == [action["kind"]]
    assert spoken["result"]["action"] == action["kind"]
    assert spoken["result"]["navigation"] == manual["result"]["navigation"]
    assert spoken["result"]["spoken"]


def test_captions_and_voice_use_the_one_settings_path(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        first = voice(client, conversation, "Hide captions.")
        result = first.json()["result"]
        assert result["kind"] == "executed"
        assert result["action"] == "SetCaptionsVisible"
        assert result["settings"]["captions_visible"] is False
        assert result["settings"]["revision"] == 1
        assert result["spoken"] == "Captions hidden."
        assert counts(path) == (1, 0, 1)

        duplicate = voice(client, conversation, " Hide   captions. ")
        assert duplicate.json() == first.json()
        assert counts(path) == (1, 0, 1)

        spoken = voice(client, conversation, "Use the Approved Narrator voice.", request="r2", revision=1)
        voice_result = spoken.json()["result"]
        assert voice_result["settings"]["approved_voice_id"] == "approved"
        assert voice_result["settings"]["revision"] == 2
        assert voice_result["spoken"] == "Voice set to Approved Narrator for the next call."
        assert counts(path) == (2, 0, 2)
        assert client.get("/v1/settings").json()["approved_voice_id"] == "approved"


def test_unapproved_spoken_voice_is_refused_without_a_write(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, "Use the British female voice.")
        result = response.json()["result"]
        assert result["kind"] == "unsupported"
        assert result["detail"] == "VOICE_NOT_FOUND"
        assert "British female" in result["spoken"]
        assert counts(path) == (0, 0, 0)


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Hide captions.", {"kind": "SetCaptionsVisible", "visible": False}),
        ("Use the Approved Narrator voice.", {"kind": "SelectApprovedVoice", "voice_id": "approved"}),
    ],
)
def test_settings_rows_converge_for_voice_and_gui(tmp_path, text, action):
    spoken_app, spoken_path = app_for(tmp_path, "spoken")
    with TestClient(spoken_app) as client:
        conversation = chat(client)
        spoken = voice(client, conversation, text).json()["result"]
        spoken_settings = client.get("/v1/settings").json()
        spoken_counts = counts(spoken_path)

    gui_app, gui_path = app_for(tmp_path, "gui")
    with TestClient(gui_app) as client:
        client.post("/v1/demo-session")
        manual = client.post(
            "/v1/settings/actions",
            json={"request_id": "gui-1", "expected_revision": 0, "action": action},
        ).json()
        gui_settings = client.get("/v1/settings").json()
        gui_counts = counts(gui_path)

    assert spoken_settings == gui_settings
    assert spoken_counts == gui_counts == (1, 0, 1)
    assert spoken["settings"] == manual["settings"]


@pytest.mark.parametrize("text", ["Speak slower.", "Speak faster."])
def test_speed_is_unsupported_until_it_reaches_the_voice(tmp_path, text):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, text)
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["kind"] == "unsupported"
        assert result["detail"] == "SPEED_APPLICATION_UNAVAILABLE"
        assert "Nothing was changed" in result["spoken"]
        assert result["settings"] is None
        assert client.get("/v1/settings").json()["speaking_speed"] == 1.0
        assert counts(path) == (0, 0, 0)


def test_duplicate_finalized_navigation_applies_one_effect(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        first = voice(client, conversation, "Show my previous conversations.")
        # A replayed final transcript carries the same source request id.
        duplicate = voice(client, conversation, " Show   my previous conversations. ")
        assert duplicate.json() == first.json()
        assert handler_trace(path) == ["ShowConversationHistory"]
        assert counts(path) == (0, 1, 0)


UNSUPPORTED_ROWS = [
    ("Show my memories about Paris.", "MEMORY_SEARCH_UNSUPPORTED", "I can't search your memories by voice yet."),
    ("What do you remember about Paris?", "MEMORY_SEARCH_UNSUPPORTED", "I can't search your memories by voice yet."),
    (
        "Forget the Paris trip but keep the budget.",
        "MEMORY_MUTATION_UNSUPPORTED",
        "I can't change or delete memories by voice yet. Nothing was changed.",
    ),
    (
        "Delete the memory of the Paris trip.",
        "MEMORY_MUTATION_UNSUPPORTED",
        "I can't change or delete memories by voice yet. Nothing was changed.",
    ),
    (
        "Give shorter answers from now on.",
        "RESPONSE_PREFERENCE_UNSUPPORTED",
        "I can't change answer length by voice yet.",
    ),
    (
        "Make your answers shorter.",
        "RESPONSE_PREFERENCE_UNSUPPORTED",
        "I can't change answer length by voice yet.",
    ),
    ("Be more concise.", "RESPONSE_PREFERENCE_UNSUPPORTED", "I can't change answer length by voice yet."),
    (
        "Clear context.",
        "MEMORY_SCOPE_REQUIRED",
        "Clearing context needs an explicit scope and is not available by voice yet. Nothing was changed.",
    ),
]


@pytest.mark.parametrize(("text", "detail", "spoken"), UNSUPPORTED_ROWS)
def test_unwired_rows_refuse_with_the_exact_sentence_and_mutate_nothing(tmp_path, text, detail, spoken):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, text)
        assert response.status_code == 200
        body = response.json()
        assert body["handled"] is True
        assert body["result"]["kind"] == "unsupported"
        assert body["result"]["detail"] == detail
        assert body["result"]["spoken"] == spoken
        assert "I searched your memories" not in body["result"]["spoken"]
        assert body["result"]["action"] is None
        assert body["result"]["settings"] is None
        assert body["result"]["changed"] is False
        assert client.get("/v1/settings").json()["revision"] == 0
        assert counts(path) == (0, 0, 0)


def test_unknown_spoken_voice_is_refused_with_the_exact_sentence(tmp_path):
    app, path = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        result = voice(client, conversation, "Use the British female voice.").json()["result"]
        assert result["kind"] == "unsupported"
        assert result["detail"] == "VOICE_NOT_FOUND"
        assert result["spoken"] == "I couldn't find an approved voice called British female."
        assert counts(path) == (0, 0, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hide the captions.", "Captions hidden."),
        ("Turn off captions.", "Captions hidden."),
        ("Turn the captions off.", "Captions hidden."),
        ("Show the captions.", "Captions shown."),
        ("Turn on captions.", "Captions shown."),
    ],
)
def test_caption_paraphrases_are_handled_instead_of_falling_through(tmp_path, text, expected):
    app, _ = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        request, revision = "r1", 0
        if text.startswith("Show") or text.startswith("Turn on"):
            # Start from the opposite state so the paraphrase is a real change.
            voice(client, conversation, "Hide captions.")
            request, revision = "r2", 1
        result = voice(client, conversation, text, request=request, revision=revision).json()["result"]
        assert result["kind"] == "executed"
        assert result["spoken"] == expected
        assert result["settings"] is not None


def test_history_paraphrase_navigates_instead_of_falling_through(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        result = voice(client, conversation, "Open the conversation history.").json()["result"]
        assert result["kind"] == "executed"
        assert result["navigation"] == "history"


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
        assert counts(path) == (0, 0, 0)


def test_stale_revision_does_not_block_navigation(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        response = voice(client, conversation, "Show my previous conversations.", revision=9)
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["kind"] == "executed"
        assert result["navigation"] == "history"


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
        assert command["settings"]["captions_visible"] is False
        assert command["settings"]["revision"] == 1
        assert command["spoken"] == "Captions hidden."
        duplicate = client.post("/internal/agent/command", headers=headers, json={"turn_id": turn_id})
        assert duplicate.json() == first.json()
        assert client.get("/v1/settings").json()["captions_visible"] is False
        assert counts(file) == (1, 0, 1)
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
