"""Onboarding reducer, HTTP and worker-greeting contracts."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.domain.onboarding import (
    GREETING,
    AcknowledgeCapabilities,
    AcknowledgeVoice,
    ChoosePace,
    ChooseVoice,
    GreetingPlayed,
    OnboardingCapabilities,
    OnboardingError,
    OnboardingProgress,
    OnboardingStage,
    Pace,
    Resume,
    Skip,
    Start,
    SubmitName,
    Suspend,
    initial_progress,
    reduce,
)
from app.main import create_app
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

AT = "2026-11-14T09:00:00+00:00"
PACE_ON = OnboardingCapabilities(speaking_pace=True)


def at_stage(stage: OnboardingStage) -> OnboardingProgress:
    progress = initial_progress(AT)
    if stage is OnboardingStage.GREETING:
        return progress
    progress = reduce(progress, GreetingPlayed("greeting"), at=AT)
    if stage is OnboardingStage.PREFERRED_NAME:
        return progress
    progress = reduce(progress, SubmitName("name", "Sam"), at=AT)
    if stage is OnboardingStage.VOICE_CHOICE:
        return progress
    progress = reduce(progress, ChooseVoice("voice", "voice_a"), at=AT, capabilities=PACE_ON)
    if stage is OnboardingStage.AWAITING_VOICE_ACK:
        return OnboardingProgress(
            stage=stage, revision=progress.revision, preferred_name="Sam", suspended_from=None, started_at=AT
        )
    if stage is OnboardingStage.SPEAKING_PACE:
        return progress
    progress = reduce(progress, ChoosePace("pace", Pace.FASTER), at=AT, capabilities=PACE_ON)
    if stage is OnboardingStage.CAPABILITY_INTRO:
        return progress
    return reduce(progress, AcknowledgeCapabilities("done"), at=AT)


def test_full_path_records_name_and_terminal_once():
    progress = at_stage(OnboardingStage.CAPABILITY_INTRO)
    assert progress.preferred_name == "Sam"
    assert progress.selected_voice_id == "voice_a"
    assert progress.selected_pace is Pace.FASTER
    completed = reduce(progress, AcknowledgeCapabilities("finished"), at=AT)
    assert completed.stage is OnboardingStage.COMPLETED
    assert completed.completed_at == AT
    assert reduce(completed, SubmitName("late", "Other"), at=AT) is completed


def test_pace_is_skipped_when_the_runtime_cannot_honour_it():
    progress = initial_progress(AT)
    progress = reduce(progress, GreetingPlayed("g"), at=AT)
    progress = reduce(progress, SubmitName("n", "Sam"), at=AT)
    progress = reduce(progress, ChooseVoice("v", None), at=AT)
    assert progress.stage is OnboardingStage.CAPABILITY_INTRO
    with pytest.raises(OnboardingError, match="ONBOARDING_TRANSITION_INVALID"):
        reduce(progress, ChoosePace("p", Pace.FASTER), at=AT)


def test_name_is_normalized_and_rejected_when_empty():
    progress = at_stage(OnboardingStage.PREFERRED_NAME)
    saved = reduce(progress, SubmitName("n", "  Ada   Lovelace "), at=AT)
    assert saved.preferred_name == "Ada Lovelace"
    with pytest.raises(OnboardingError, match="ONBOARDING_NAME_INVALID"):
        reduce(progress, SubmitName("empty", "   "), at=AT)


def test_same_event_id_with_different_payload_is_a_conflict():
    progress = reduce(at_stage(OnboardingStage.PREFERRED_NAME), SubmitName("e", "Sam"), at=AT)
    assert reduce(progress, SubmitName("e", "Sam"), at=AT) is progress
    with pytest.raises(OnboardingError, match="ONBOARDING_EVENT_CONFLICT"):
        reduce(progress, SubmitName("e", "Other"), at=AT)


SKIPPABLE = sorted(
    stage.value
    for stage in OnboardingStage
    if stage not in {OnboardingStage.NOT_STARTED, OnboardingStage.COMPLETED, OnboardingStage.SKIPPED}
)


def progress_at(stage: OnboardingStage) -> OnboardingProgress:
    if stage is OnboardingStage.SUSPENDED:
        return reduce(at_stage(OnboardingStage.PREFERRED_NAME), Suspend("suspend"), at=AT)
    return at_stage(stage)


@given(st.sampled_from(SKIPPABLE))
def test_skip_is_available_at_every_non_terminal_stage_and_is_absorbing(stage_value):
    progress = progress_at(OnboardingStage(stage_value))
    result = reduce(progress, Skip(f"skip-{stage_value}"), at=AT)
    assert result.stage is OnboardingStage.SKIPPED
    assert reduce(result, SubmitName("after-skip", "Sam"), at=AT) is result


SUSPENDABLE = [
    stage.value
    for stage in OnboardingStage
    if stage
    not in {OnboardingStage.NOT_STARTED, OnboardingStage.SUSPENDED, OnboardingStage.COMPLETED, OnboardingStage.SKIPPED}
]


@given(st.sampled_from(SUSPENDABLE))
def test_suspend_and_resume_return_to_the_same_stage(stage_value):
    progress = at_stage(OnboardingStage(stage_value))
    suspended = reduce(progress, Suspend(f"s-{stage_value}"), at=AT)
    assert suspended.stage is OnboardingStage.SUSPENDED
    assert suspended.suspended_from is progress.stage
    resumed = reduce(suspended, Resume(f"r-{stage_value}"), at=AT)
    assert resumed.stage is progress.stage
    assert resumed.suspended_from is None


@given(st.sampled_from(["Sam", "Ada Lovelace", "  Zoë  "]))
def test_duplicate_events_never_save_twice(name):
    progress = at_stage(OnboardingStage.PREFERRED_NAME)
    event = SubmitName("same-id", name)
    once = reduce(progress, event, at=AT)
    twice = reduce(once, event, at=AT)
    assert twice is once
    assert twice.revision == once.revision
    assert twice.preferred_name == once.preferred_name


def test_awaiting_voice_ack_can_be_continued_without_claiming_a_switch():
    progress = at_stage(OnboardingStage.AWAITING_VOICE_ACK)
    continued = reduce(progress, AcknowledgeVoice("ack"), at=AT, capabilities=PACE_ON)
    assert continued.stage is OnboardingStage.SPEAKING_PACE
    assert continued.selected_voice_id is None


def test_start_is_idempotent():
    progress = initial_progress(AT)
    assert reduce(progress, Start("start"), at=AT) is progress


# --- HTTP contract -------------------------------------------------------


def settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/onboarding.db",
        "openai_api_key": "",
        "deepseek_api_key": "",
        "deepgram_api_key": "",
        "eleven_api_key": "",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def event(client, event_id, kind, **payload):
    return client.post("/v1/onboarding/events", json={"event_id": event_id, "type": kind, **payload})


def test_onboarding_requires_a_demo_identity(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/v1/onboarding").status_code == 401


def test_first_get_starts_and_completed_onboarding_does_not_repeat(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/v1/demo-session")
        first = client.get("/v1/onboarding").json()
        assert first["stage"] == "greeting"
        assert first["greeting"] == GREETING
        assert client.get("/v1/onboarding").json()["revision"] == first["revision"]
        assert event(client, "g", "greeting_played").json()["stage"] == "preferred_name"
        assert event(client, "n", "submit_name", name="Sam").json()["preferred_name"] == "Sam"
        assert event(client, "v", "choose_voice").json()["stage"] == "capability_intro"
        assert event(client, "done", "acknowledge_capabilities").json()["stage"] == "completed"
        # A reconnect is still the same owner: no greeting and no repeat.
        second = client.get("/v1/onboarding").json()
        assert second["stage"] == "completed"
        assert second["greeting"] is None


def test_duplicate_event_ids_do_not_double_save_a_name_or_voice(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/v1/demo-session")
        event(client, "g", "greeting_played")
        assert event(client, "n", "submit_name", name="Sam").json()["revision"] == 3
        duplicate = event(client, "n", "submit_name", name="Sam").json()
        assert duplicate["revision"] == 3 and duplicate["preferred_name"] == "Sam"
        assert event(client, "v", "choose_voice", voice_id="voice_a").status_code == 200
        assert client.get("/v1/settings").json()["revision"] == 1
        event(client, "v", "choose_voice", voice_id="voice_a")
        assert client.get("/v1/settings").json()["revision"] == 1
        conflict = event(client, "v", "choose_voice", voice_id="voice_b")
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "ONBOARDING_EVENT_CONFLICT"


def test_skip_discards_uncommitted_name_and_writes_no_memory(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/v1/demo-session")
        event(client, "g", "greeting_played")
        skipped = event(client, "skip", "skip").json()
        assert skipped["stage"] == "skipped"
        assert skipped["preferred_name"] is None
        assert client.get("/v1/settings").json()["revision"] == 0
        assert event(client, "late", "submit_name", name="Sam").json()["stage"] == "skipped"
    with closing(sqlite3.connect(tmp_path / "onboarding.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM onboarding_progress").fetchone() == (1,)


def test_suspend_and_resume_keep_the_stage(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/v1/demo-session")
        event(client, "g", "greeting_played")
        suspended = event(client, "s", "suspend").json()
        assert suspended["stage"] == "suspended" and suspended["suspended_from"] == "preferred_name"
        resumed = event(client, "r", "resume").json()
        assert resumed["stage"] == "preferred_name" and resumed["suspended_from"] is None
        assert event(client, "r", "resume").status_code == 200
        assert event(client, "r2", "resume").status_code == 409


def test_invalid_name_and_invalid_transition_are_honest(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/v1/demo-session")
        assert event(client, "early", "submit_name", name="Sam").status_code == 409
        event(client, "g", "greeting_played")
        empty = event(client, "empty", "submit_name", name="   ")
        assert empty.status_code == 422
        assert empty.json()["detail"]["code"] == "ONBOARDING_NAME_INVALID"


def test_pace_is_hidden_unless_the_runtime_supports_it(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/v1/demo-session")
        assert client.get("/v1/onboarding").json()["capabilities"]["speaking_pace"] is False
        event(client, "g", "greeting_played")
        event(client, "n", "submit_name", name="Sam")
        assert event(client, "v", "choose_voice").json()["stage"] == "capability_intro"
        assert event(client, "p", "choose_pace", pace="faster").status_code == 409


def test_pace_stage_and_setting_when_supported(tmp_path):
    with TestClient(create_app(settings(tmp_path, speaking_pace_supported=True))) as client:
        client.post("/v1/demo-session")
        assert client.get("/v1/onboarding").json()["capabilities"]["speaking_pace"] is True
        event(client, "g", "greeting_played")
        event(client, "n", "submit_name", name="Sam")
        assert event(client, "v", "choose_voice").json()["stage"] == "speaking_pace"
        assert event(client, "p", "choose_pace", pace="faster").json()["stage"] == "capability_intro"
        assert client.get("/v1/settings").json()["speaking_speed"] == 1.1


def test_worker_bootstrap_greets_once_and_marks_the_intro(tmp_path):
    app = create_app(settings(tmp_path, agent_service_secret="secret"))
    with TestClient(app) as client:
        client.post("/v1/demo-session")
        conversation = client.post("/v1/conversations").json()["id"]
        with closing(sqlite3.connect(tmp_path / "onboarding.db")) as connection:
            owner = connection.execute("SELECT id FROM identities").fetchone()[0]
            connection.execute(
                "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline,"
                "room_cleanup_completed_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    "session",
                    conversation,
                    owner,
                    "room",
                    "active",
                    (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    json.dumps({}),
                    None,
                ),
            )
            connection.commit()
        headers = {"x-agent-secret": "secret", "x-session-id": "session", "x-room-name": "room"}
        bootstrap = client.post("/internal/agent/bootstrap", headers=headers)
        assert bootstrap.status_code == 200
        assert bootstrap.json()["onboarding_greeting"] == GREETING
        assert client.post("/internal/agent/onboarding/greeting-played", headers=headers).status_code == 204
        assert client.post("/internal/agent/onboarding/greeting-played", headers=headers).status_code == 204
        progress = client.get("/v1/onboarding").json()
        assert progress["stage"] == "preferred_name"
        assert progress["intro_played_at"] is not None
        # A second connection is told not to replay the greeting.
        again = client.post("/internal/agent/bootstrap", headers=headers).json()
        assert again["onboarding_greeting"] is None
