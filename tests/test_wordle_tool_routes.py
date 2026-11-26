"""Model-facing Wordle tools: owner game reuse, secrecy, enablement and replay."""

from app.config import Settings
from app.main import create_app as real_create_app
from fastapi.testclient import TestClient
from test_context_assembly import start_turn
from test_context_workflow import FakeEmbedder, seed
from test_wordle_source import FixtureSource

PLAY = "/internal/agent/tool/wordle-play"
GUESS = "/internal/agent/tool/wordle-guess"
STATUS = "/internal/agent/tool/wordle-status"
CLOSE = "/internal/agent/tool/wordle-close"
RESET = "/internal/agent/tool/wordle-reset"


def build(tmp_path, *, enabled=True):
    file = tmp_path / "tools.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{file}",
        agent_service_secret="test-service-secret",
        context_proposal_secret="proposal-secret",
        wordle_enabled=enabled,
        _env_file=None,
    )
    return file, real_create_app(settings, embedding_adapter=FakeEmbedder(), wordle_source=FixtureSource())


def begin(client, file):
    owner = client.post("/v1/demo-session").json()["id"]
    chat = client.post("/v1/conversations").json()["id"]
    seed(file, chat, [])
    headers, first = start_turn(client, chat, owner, file, memories=(), text="Can we play the daily Wordle?")
    return headers, first["turn_id"]


def call(client, headers, path, turn, key, **arguments):
    return client.post(path, headers=headers, json={"turn_id": turn, "request_id": key, **arguments})


def test_play_guess_status_reuse_the_owner_game_and_hide_the_answer(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        play = call(client, headers, PLAY, turn, "p1").json()
        assert play["status"] == "in_progress" and play["ui"] == "open"
        assert "6 guesses left" in play["message"]
        guess = call(client, headers, GUESS, turn, "g1", word="slate").json()
        assert guess["spoken"] == "SLATE: S gray, L gray, A green, T gray, E green. 5 guesses left."
        assert play["spoken"] == "Today's Wordle is ready. 6 guesses left."
        assert guess["ui"] == "open" and "5 guesses left" in guess["message"]
        assert "S gray" in guess["message"] and "A green" in guess["message"]
        # The web client reads the same server game the tool just changed.
        board = client.get("/v1/wordle/today").json()["game"]
        assert [row["word"] for row in board["guesses"]] == ["SLATE"]
        status = call(client, headers, STATUS, turn, "s1").json()
        assert status["ui"] == "refresh" and "SLATE" in status["message"]
        for body in (play, guess, status):
            assert "CRANE" not in str(body)
        won = call(client, headers, GUESS, turn, "g2", word="crane").json()
        assert won["spoken"] == "You got it in 2 guesses. Well done."
        assert won["status"] == "won" and "won" in won["message"]
        over = call(client, headers, GUESS, turn, "g3", word="plant").json()
        assert over["status"] == "error" and over["code"] == "WORDLE_GAME_OVER"


def test_spoken_reply_reads_only_the_latest_guess_unless_the_board_is_asked_for(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        call(client, headers, GUESS, turn, "a", word="slate")
        second = call(client, headers, GUESS, turn, "b", word="plant").json()
        assert second["spoken"].startswith("PLANT:") and "SLATE" not in second["spoken"]
        assert second["spoken"].endswith("4 guesses left.")
        repeat = call(client, headers, GUESS, turn, "c", word="slate").json()
        assert repeat["spoken"] == "You already tried SLATE. No attempt used. 4 guesses left."
        resumed = call(client, headers, PLAY, turn, "d").json()
        assert resumed["spoken"] == "Today's Wordle is ready. 4 guesses left."
        status = call(client, headers, STATUS, turn, "e").json()
        assert "SLATE" in status["spoken"] and "PLANT" in status["spoken"]


def test_guess_is_idempotent_and_invalid_words_are_reported(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        first = call(client, headers, GUESS, turn, "same", word="slate").json()
        replay = call(client, headers, GUESS, turn, "same", word="slate").json()
        assert replay == first
        assert len(client.get("/v1/wordle/today").json()["game"]["guesses"]) == 1
        repeat = call(client, headers, GUESS, turn, "again", word="SLATE").json()
        assert repeat["status"] == "repeat" and "already tried SLATE" in repeat["message"]
        assert "no attempt was used" in repeat["message"]
        assert len(client.get("/v1/wordle/today").json()["game"]["guesses"]) == 1
        bad = call(client, headers, GUESS, turn, "bad", word="cat").json()
        assert bad["status"] == "error" and bad["code"] == "WORDLE_GUESS_INVALID"


def test_not_in_list_guess_is_rejected_without_recording(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        call(client, headers, PLAY, turn, "p1")
        rejected = call(client, headers, GUESS, turn, "not-listed", word="zzzzz").json()
        assert rejected["status"] == "error" and rejected["code"] == "WORDLE_NOT_IN_LIST"
        assert rejected["spoken"] == "ZZZZZ isn't in the word list. Try another word."
        assert "not in the Wordle word list" in rejected["message"]
        game = client.get("/v1/wordle/today").json()["game"]
        assert game["guesses"] == [] and game["attempts_remaining"] == 6


def test_todays_answer_is_accepted_when_absent_from_the_allowed_list(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.wordle.allowed_words", lambda: frozenset())
    monkeypatch.setattr("app.services.wordle_tools.allowed_words", lambda: frozenset())
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        won = call(client, headers, GUESS, turn, "answer-not-listed", word="crane").json()
        assert won["status"] == "won"


def test_confirm_guess_returns_the_payload_and_records_nothing(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        call(client, headers, PLAY, turn, "p1")
        confirmed = call(client, headers, GUESS, turn, "c1", word="crane", confirm=True).json()
        assert confirmed == {
            "status": "confirm",
            "ui": "confirm_guess",
            "word": "CRANE",
            "spoken": "C, R, A, N, E. Is that your guess?",
            "message": "Asked the user to confirm CRANE. Nothing is recorded until they say yes.",
        }
        game = client.get("/v1/wordle/today").json()["game"]
        assert game["guesses"] == [] and game["attempts_remaining"] == 6

        call(client, headers, GUESS, turn, "g1", word="slate")
        repeat = call(client, headers, GUESS, turn, "c2", word="slate", confirm=True).json()
        assert repeat["status"] == "repeat" and "already tried SLATE" in repeat["message"]
        assert len(client.get("/v1/wordle/today").json()["game"]["guesses"]) == 1


def test_confirm_guess_rejects_a_not_in_list_word_without_recording(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        call(client, headers, PLAY, turn, "p1")
        rejected = call(client, headers, GUESS, turn, "c1", word="zzzzz", confirm=True).json()
        assert rejected["status"] == "error" and rejected["code"] == "WORDLE_NOT_IN_LIST"
        assert rejected["spoken"] == "ZZZZZ isn't in the word list. Try another word."
        game = client.get("/v1/wordle/today").json()["game"]
        assert game["guesses"] == [] and game["attempts_remaining"] == 6


def test_close_asks_the_client_to_close_and_disabled_reports_off(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        assert call(client, headers, CLOSE, turn, "c1").json()["ui"] == "close"
    (tmp_path / "off").mkdir()
    file, app = build(tmp_path / "off", enabled=False)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        body = call(client, headers, PLAY, turn, "p1").json()
        assert body["status"] == "disabled" and "ui" not in body


def test_tool_routes_require_the_worker_secret(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        response = client.post(PLAY, json={"turn_id": "t", "request_id": "r"})
        assert response.status_code in {401, 403, 404}


def test_reset_tool_only_requests_confirmation_and_never_resets(tmp_path):
    file, app = build(tmp_path)
    with TestClient(app) as client:
        headers, turn = begin(client, file)
        call(client, headers, GUESS, turn, "g1", word="slate")
        asked = call(client, headers, RESET, turn, "r1").json()
        assert asked["status"] == "confirm_required" and asked["ui"] == "confirm_reset"
        assert asked["spoken"] == "Reset today's game? Say yes to confirm."
        assert [row["word"] for row in client.get("/v1/wordle/today").json()["game"]["guesses"]] == ["SLATE"]
        assert call(client, headers, RESET, turn, "r1").json() == asked  # ledger replay
