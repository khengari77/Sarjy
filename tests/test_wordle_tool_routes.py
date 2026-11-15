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
        assert won["status"] == "won" and "won" in won["message"]
        over = call(client, headers, GUESS, turn, "g3", word="plant").json()
        assert over["status"] == "error" and over["code"] == "WORDLE_GAME_OVER"


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
