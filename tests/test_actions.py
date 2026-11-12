import sqlite3

import pytest
from app.config import Settings
from app.domain.commands import parse_exact_command
from app.main import create_app
from app.services.voices import VoiceCatalogError, VoiceOption
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st


class Catalog:
    async def require(self, voice_id):
        if voice_id != "approved":
            raise VoiceCatalogError("VOICE_UNAVAILABLE", 422)
        return VoiceOption(id="approved", name="Approved")


def setup(tmp_path):
    path = tmp_path / "actions.db"
    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{path}", _env_file=None), voice_catalog=Catalog())
    return app, path


def chat(client):
    client.post("/v1/demo-session")
    return client.post("/v1/conversations").json()["id"]


def submit(client, conversation, request="r1", revision=0, **extra):
    return client.post(
        "/v1/actions",
        json={"conversation_id": conversation, "request_id": request, "expected_revision": revision, **extra},
    )


@pytest.mark.parametrize(
    "text",
    [
        "do not speak slower",
        '"speak slower"',
        "'speak slower'",
        "speak slower and hide captions",
        "please explain speak slower",
        "speak slower?",
        "hide captions; show captions",
        "not hide captions",
    ],
)
def test_nonexact_commands_do_not_parse(text):
    assert parse_exact_command(text) is None


@given(st.sampled_from(["speak slower", "speak faster", "hide captions", "show captions", "open the memory explorer"]))
def test_quoted_negated_and_compound_commands_never_parse(command):
    for text in ['"' + command + '"', "do not " + command, command + " and hide captions"]:
        assert parse_exact_command(text) is None


def test_ui_and_final_transcript_share_persisted_settings_and_idempotency(tmp_path):
    app, path = setup(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        first = submit(client, conversation, source="ui", action={"kind": "AdjustSpeakingSpeed", "delta": -0.1})
        assert first.status_code == 200
        assert first.json()["result"]["settings"]["speaking_speed"] == 0.9
        retry = submit(client, conversation, source="transcript", transcript=" Speak   slower. ")
        assert retry.json() == first.json()
        next_response = submit(
            client, conversation, request="r2", revision=1, source="transcript", transcript="speak slower"
        )
        assert next_response.json()["result"]["settings"]["speaking_speed"] == 0.8
        assert client.get("/v1/settings").json()["revision"] == 2
        assert (
            submit(
                client,
                conversation,
                request="stale",
                source="ui",
                action={"kind": "SetCaptionsVisible", "visible": False},
            ).status_code
            == 409
        )
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM setting_revisions").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM context_operations").fetchone()[0] == 2


def test_unsafe_inputs_noops_and_invalid_actions_have_no_effects(tmp_path):
    app, path = setup(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        assert submit(client, conversation, source="transcript", transcript="do not hide captions").json() == {
            "handled": False
        }
        assert (
            submit(client, conversation, source="transcript", transcript="hide captions", finalized=False).status_code
            == 422
        )
        assert submit(client, conversation, source="ui", action={"kind": "DeleteEverything"}).status_code == 422
        result = submit(client, conversation, source="ui", action={"kind": "SetSpeakingSpeed", "speed": 1.0}).json()
        assert result["result"]["changed"] is False
        assert client.get("/v1/settings").json()["revision"] == 0
        assert (
            submit(
                client, conversation, source="ui", action={"kind": "SetCaptionsVisible", "visible": False}
            ).status_code
            == 409
        )
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM setting_revisions").fetchone()[0] == 0


def test_owner_checks_and_approved_voice_validation(tmp_path):
    app, path = setup(tmp_path)
    with TestClient(app) as alice:
        conversation = chat(alice)
        bob = TestClient(app)
        chat(bob)
        assert (
            submit(bob, conversation, source="ui", action={"kind": "SetCaptionsVisible", "visible": False}).status_code
            == 404
        )
        assert (
            submit(
                alice, conversation, source="ui", action={"kind": "SelectApprovedVoice", "voice_id": "missing"}
            ).status_code
            == 422
        )
        assert alice.get("/v1/settings").json()["revision"] == 0
        result = submit(
            alice, conversation, source="ui", action={"kind": "SelectApprovedVoice", "voice_id": "approved"}
        )
        assert result.json()["result"]["settings"]["approved_voice_id"] == "approved"


def test_context_actions_only_use_registered_authorized_handler(tmp_path):
    app, path = setup(tmp_path)
    effects = []

    async def handler(**kwargs):
        effects.append((kwargs["owner_id"], kwargs["conversation_id"], kwargs["action"].query))
        return {"proposal_id": "p1", "confirmed": False}

    with TestClient(app) as client:
        conversation = chat(client)
        assert (
            submit(
                client, conversation, source="ui", action={"kind": "ProposeContextExclusion", "query": "Paris"}
            ).status_code
            == 503
        )
        app.state.action_handlers["ProposeContextExclusion"] = handler
        result = submit(client, conversation, source="ui", action={"kind": "ProposeContextExclusion", "query": "Paris"})
        assert result.json()["result"]["confirmed"] is False
        assert (
            submit(
                client, conversation, source="ui", action={"kind": "ProposeContextExclusion", "query": "Paris"}
            ).json()
            == result.json()
        )
        assert len(effects) == 1


def test_anonymous_requests_and_owner_spoofing_are_rejected(tmp_path):
    app, path = setup(tmp_path)
    with TestClient(app) as client:
        assert client.get("/v1/settings").status_code == 401
        assert submit(client, "missing", source="ui", action={"kind": "ShowConversationHistory"}).status_code == 401
        conversation = chat(client)
        assert (
            submit(
                client, conversation, owner_id="another-owner", source="ui", action={"kind": "ShowConversationHistory"}
            ).status_code
            == 422
        )


def test_concurrent_duplicate_submission_applies_setting_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    app, path = setup(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)

        def action(_):
            return submit(client, conversation, source="ui", action={"kind": "AdjustSpeakingSpeed", "delta": -0.1})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(action, range(2)))
        assert all(result.status_code == 200 for result in results)
        assert results[0].json() == results[1].json()
        assert client.get("/v1/settings").json()["speaking_speed"] == 0.9
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM setting_revisions").fetchone()[0] == 1


def test_nonfinite_action_arguments_fail_validation_without_mutation(tmp_path):
    import json

    app, path = setup(tmp_path)
    with TestClient(app) as client:
        conversation = chat(client)
        for value in [float("inf"), float("-inf"), float("nan")]:
            payload = {
                "conversation_id": conversation,
                "request_id": "bad",
                "expected_revision": 0,
                "source": "ui",
                "action": {"kind": "AdjustSpeakingSpeed", "delta": value},
            }
            response = client.post(
                "/v1/actions", content=json.dumps(payload), headers={"content-type": "application/json"}
            )
            assert response.status_code == 422
        assert client.get("/v1/settings").json()["revision"] == 0
