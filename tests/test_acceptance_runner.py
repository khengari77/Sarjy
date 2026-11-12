import asyncio
import importlib.util
import json
import stat
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("acceptance", Path(__file__).parents[1] / "scripts/day1_acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize(
    "used,monthly,active,expected",
    [(0, 0, 0, ["S1", "S2", "S3"]), (2, 0, 0, ["S1"]), (3, 0, 0, []), (4, 0, 0, []), (0, 80, 0, []), (0, 0, 1, [])],
)
def test_allowance_never_adds_reservations(used, monthly, active, expected):
    assert runner.allocation(used, monthly, active) == expected


def test_missing_evidence_cannot_pass():
    assert runner.aggregate("passed", "pending") == "pending"
    assert runner.aggregate("pending", "failed") == "failed"
    assert runner.aggregate("passed", "passed") == "passed"
    assert runner.aggregate() == "pending"


def test_accounting_handles_day_rollover_and_new_providers():
    before = [{"day": "2026-11-12", "provider": "openai", "reserved_units": 10}]
    after = before + [{"day": "2026-11-13", "provider": "deepseek", "reserved_units": 7}]
    assert runner.ledger_delta(before, after) == [
        {"day": "2026-11-12", "provider": "openai", "delta": 0},
        {"day": "2026-11-13", "provider": "deepseek", "delta": 7},
    ]


def turn(identifier, role, text, status="completed"):
    return {"id": identifier, "role": role, "text": text, "status": status}


def test_continuity_requires_order_roles_and_completed_turns():
    turns = [
        turn("1", "user", "My lucky number is 4172"),
        turn("2", "user", "What is my lucky number?"),
        turn("3", "assistant", "4172"),
        turn("4", "user", "Multiply it by two"),
        turn("5", "assistant", "8344"),
    ]
    assert runner.continuity(turns) == "passed"
    assert runner.continuity(list(reversed(turns))) == "failed"
    assert runner.continuity(turns[:-1]) == "failed"
    turns[-1]["status"] = "cancelled"
    assert runner.continuity(turns) == "failed"


def test_history_is_private_and_detects_partial_duplicate_turns():
    turns = [turn("1", "user", "private transcript")]
    evidence = runner.summarize_turns(turns)
    assert "private transcript" not in json.dumps(evidence)
    assert evidence["complete_unique"] == "passed"
    assert runner.summarize_turns(turns * 2)["complete_unique"] == "failed"
    turns[0]["status"] = "partial"
    assert runner.summarize_turns(turns)["complete_unique"] == "failed"


def test_weather_matches_actual_spoken_temperature_and_completed_tool():
    tools = [
        {
            "name": "get_current_weather",
            "result": {
                "city": "Tripoli",
                "country": "Libya",
                "timezone": "Africa/Tripoli",
                "source": "Open-Meteo",
                "current": {"temperature_2m": 23},
            },
        }
    ]
    assert runner.weather_result(tools, 23)["result"] == "passed"
    assert runner.weather_result(tools, 24)["result"] == "failed"
    assert runner.weather_result(tools, None)["result"] == "pending"
    tools[0]["state"] = "unknown"
    assert runner.weather_result(tools, 23)["result"] == "failed"


class SessionReader:
    def __init__(self, states):
        self.states = iter(states)

    async def session(self, owner, session, supported):
        return next(self.states)


def test_cleanup_missing_schema_is_pending_and_orphan_fails():
    row = {"status": "active", "room_cleanup_completed_at": None}
    assert asyncio.run(runner.cleanup_readback(SessionReader([row]), "o", "s", False))["result"] == "pending"
    assert asyncio.run(runner.cleanup_readback(SessionReader([row]), "o", "s", True))["result"] == "failed"
    assert asyncio.run(runner.cleanup_readback(SessionReader([None]), "o", "s", True))["result"] == "failed"


def test_cleanup_polling_observes_fresh_rows(monkeypatch):
    async def sleep(_):
        pass

    monkeypatch.setattr(runner.asyncio, "sleep", sleep)
    rows = [
        {"status": "active", "room_cleanup_completed_at": None},
        {"status": "ended", "room_cleanup_completed_at": "2026-11-13"},
    ]
    result = asyncio.run(runner.cleanup_readback(SessionReader(rows), "o", "s", True, wait=30))
    assert result["result"] == "passed"


def test_private_atomic_reports_never_mark_release_complete(tmp_path):
    path = tmp_path / "evidence" / "report.json"
    runner.save(path, {"scenarios": {}})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_suffix(".md").stat().st_mode) == 0o600
    assert json.loads(path.read_text())["gates"]["D1.8"] == "pending"
    assert set(p.name for p in path.parent.iterdir()) == {"report.json", "report.md"}


def test_resume_does_not_repair_or_replay_failed_observations(tmp_path):
    report = {"scenarios": {"S1": {"human": {"audible": "failed"}}}}
    with pytest.raises(InterruptedError):
        asyncio.run(runner.scenario(None, report, "S1", tmp_path / "report.json"))


def test_session_ownership_mismatch_is_rejected():
    reader = runner.Readback(None)

    async def rows(sql, **params):
        return [{"conversation_id": "foreign"}] if "voice_sessions" in sql else []

    reader.rows = rows
    with pytest.raises(ValueError, match="ownership"):
        asyncio.run(reader.session("owner", "session", False))


def test_schema_capabilities_do_not_require_new_tool_state():
    assert not runner.has_column({"capabilities": []}, "tool_calls", "state")


def test_error_output_never_contains_database_password(monkeypatch, capsys):
    async def fail(_):
        raise RuntimeError("postgresql://user:PRIVATE_PASSWORD_123@host/db")

    monkeypatch.setattr(runner, "execute", fail)
    monkeypatch.setattr("sys.argv", ["acceptance", "preflight", "--owner", "owner"])
    with pytest.raises(SystemExit):
        runner.main()
    assert "PRIVATE_PASSWORD_123" not in capsys.readouterr().out


def test_every_database_read_has_readonly_transaction_and_bound_parameters():
    calls = []

    class Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def begin(self):
            return Context()

        async def execute(self, sql, params=None):
            calls.append((str(sql), params))
            return self

        def mappings(self):
            return [{"id": "expected"}]

    class Engine:
        def connect(self):
            return Context()

    result = asyncio.run(runner.Readback(Engine()).rows("SELECT id WHERE owner=:owner", owner="untrusted' value"))
    assert result == [{"id": "expected"}]
    assert calls[0][0] == "SET TRANSACTION READ ONLY"
    assert calls[1][0] == "SET LOCAL statement_timeout = '5000'"
    assert calls[2] == ("SELECT id WHERE owner=:owner", {"owner": "untrusted' value"})


def test_guided_s1_produces_separate_human_and_machine_evidence(tmp_path, monkeypatch):
    turns = [
        turn("1", "user", "My lucky number is 4172"),
        turn("2", "user", "What is my lucky number?"),
        turn("3", "assistant", "4172"),
        turn("4", "user", "Multiply it by two"),
        turn("5", "assistant", "8344"),
    ]

    class Reader:
        async def preflight(self, owner):
            return {"allocation": ["S1"]}

        async def session(self, owner, session, supported):
            return {
                "id": "new-session",
                "conversation_id": "conversation",
                "status": "ended",
                "room_cleanup_completed_at": "2026-11-13",
            }

        async def turns(self, conversation):
            return turns

        async def tools(self, conversation, has_state):
            return [
                {
                    "name": "get_current_weather",
                    "state": "completed",
                    "result": {
                        "city": "Tripoli",
                        "country": "Libya",
                        "timezone": "Africa/Tripoli",
                        "source": "Open-Meteo",
                        "current": {"temperature_2m": 23},
                    },
                }
            ]

    inputs = iter(["new-session"] + ["passed"] * 6 + ["23", "passed", "passed"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    report = {
        "owner": "owner",
        "scenarios": {},
        "before": {
            "session_ids": [],
            "capabilities": [{"table_name": "voice_sessions", "column_name": "room_cleanup_completed_at"}],
        },
    }
    asyncio.run(runner.scenario(Reader(), report, "S1", tmp_path / "report.json"))
    assert report["gates"]["D1.3"] == "passed"
    assert report["gates"]["D1.4"] == "passed"
    assert report["gates"]["D1.5"] == "passed"
    assert report["gates"]["D1.6"] == "pending"
    assert "My lucky number" not in (tmp_path / "report.json").read_text()
    # Completed sessions are skipped without prompting or touching the database again.
    asyncio.run(runner.scenario(None, report, "S1", tmp_path / "report.json"))


def test_interrupted_end_is_not_replayed(tmp_path):
    report = {
        "scenarios": {
            "S1": {
                "human": {k: "passed" for k in runner.QUESTIONS["S1"]},
                "session": "s",
                "spoken_temperature": 23,
                "before_reload": {},
                "action_requested_at": "2026-11-13",
            }
        }
    }
    with pytest.raises(InterruptedError):
        asyncio.run(runner.scenario(None, report, "S1", tmp_path / "report.json"))
    assert report["scenarios"]["S1"]["cleanup"]["result"] == "pending"


def test_resume_retains_original_plan_when_capacity_shrinks(tmp_path, monkeypatch):
    from argparse import Namespace

    snapshot = {"used": 1, "ledger": [], "allocation": ["S1", "S2"], "database_revision": [{"version_num": "0004"}]}
    path = tmp_path / "report.json"
    original = {
        "schema": 2,
        "owner": "owner",
        "scenarios": {},
        "before": snapshot,
        "revisions": {"api": "deployed"},
        "plan": ["S1", "S2", "S3"],
    }
    runner.save(path, original)
    visited = []

    class Engine:
        async def dispose(self):
            pass

    class Reader:
        def __init__(self, _):
            pass

        async def preflight(self, owner):
            return {**snapshot, "used": 2, "allocation": ["S1"]}

    async def scenario(reader, report, label, path):
        visited.append(label)

    monkeypatch.setenv("ACCEPTANCE_DATABASE_URL", "postgresql+asyncpg://unused")
    monkeypatch.setattr(runner, "create_async_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(runner, "Readback", Reader)
    monkeypatch.setattr(runner, "metadata", lambda: {"api": "deployed"})
    monkeypatch.setattr(runner, "scenario", scenario)
    asyncio.run(runner.execute(Namespace(mode="resume", owner="owner", report=path)))
    assert visited == ["S1", "S2", "S3"]
    assert json.loads(path.read_text())["reservations_consumed"] == 1
    monkeypatch.setattr(runner, "metadata", lambda: {"api": "changed"})
    with pytest.raises(ValueError, match="Deployment changed"):
        asyncio.run(runner.execute(Namespace(mode="resume", owner="owner", report=path)))
    assert visited == ["S1", "S2", "S3"]


def test_resume_after_start_never_instructs_another_admission(tmp_path, monkeypatch, capsys):
    class Reader:
        async def preflight(self, owner):
            return {"allocation": []}  # Original session occupies the active slot.

        async def session(self, owner, session, supported):
            return {"conversation_id": "c"}

    inputs = iter(["original-session", "stop"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    report = {
        "owner": "owner",
        "before": {"session_ids": []},
        "scenarios": {"S1": {"human": {}, "start_requested_at": "2026-11-13"}},
    }
    with pytest.raises(InterruptedError):
        asyncio.run(runner.scenario(Reader(), report, "S1", tmp_path / "report.json"))
    assert report["scenarios"]["S1"]["session"] == "original-session"
    assert "Do not start another call" in capsys.readouterr().out


def test_lifecycle_history_accepts_intentional_assistant_interruption_only():
    interrupted = [turn("1", "assistant", "Count one, two", "interrupted")]
    assert runner.summarize_turns(interrupted)["complete_unique"] == "failed"
    assert runner.summarize_turns(interrupted, allow_interrupted=True)["complete_unique"] == "passed"
    interrupted[0]["role"] = "user"
    assert runner.summarize_turns(interrupted, allow_interrupted=True)["complete_unique"] == "failed"
    interrupted[0]["status"] = "failed"
    assert runner.summarize_turns(interrupted, allow_interrupted=True)["complete_unique"] == "failed"
