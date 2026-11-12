"""Guided, read-only acceptance evidence. Never starts a call or invokes a provider."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path("artifacts/deployment/day1-human-acceptance")
SCENARIOS = ("S1", "S2", "S3")
QUESTIONS = {
    "S1": {
        "microphone": 'Say "Hello, can you hear me? Please reply in one short sentence." Final English transcript?',
        "audible": "Relevant reply actually audible through headphones, without an error banner?",
        "clarification": 'Ask "What is the weather?" Did it ask for a city without inventing weather?',
        "weather_audio": 'Say "Tripoli, Libya." Audible answer names Tripoli and temperature?',
        "typed_continuity": 'Say "My lucky number is 4172." Type "What is my lucky number?" while live. Answer 4172?',
        "voice_continuity": 'Say "Multiply it by two." Audible answer 8344?',
    },
    "S2": {
        "denial": "Block microphone before starting. Actionable microphone error and recovery offered?",
        "recovery": "Allow microphone; recover the SAME session and confirm speech is transcribed?",
        "mute": "Mute, speak, confirm no new transcript; unmute and confirm transcription recovers?",
        "interruption": 'Ask "Count slowly from one to twenty." Stop/speak over it. Audio stops promptly?',
        "stale_output": "After at least 2 seconds, no abandoned audio/text arrives and a follow-up works?",
    },
    "S3": {
        "reconnect": "Complete a turn, go offline ~5 seconds, restore. Recovers live or ends cleanly?",
        "still_live": "Is the SAME call still live? If pending/failed, do not test refresh or start another call.",
    },
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def allocation(used: int, monthly: int, active: int) -> list[str]:
    return list(SCENARIOS[: max(0, 3 - used)]) if monthly < 80 and active == 0 else []


def aggregate(*values: str) -> str:
    if "failed" in values:
        return "failed"
    return "passed" if values and all(v == "passed" for v in values) else "pending"


def ledger_delta(before: list[dict], after: list[dict]) -> list[dict]:
    first = {(r["day"], r["provider"]): r["reserved_units"] for r in before}
    last = {(r["day"], r["provider"]): r["reserved_units"] for r in after}
    return [
        {"day": day, "provider": provider, "delta": last.get((day, provider), 0) - first.get((day, provider), 0)}
        for day, provider in sorted(first.keys() | last.keys())
    ]


def summarize_turns(turns: list[dict]) -> dict:
    """Retain hashes and metadata rather than conversation text."""
    return {
        "entries": [
            {
                "id": t["id"],
                "role": t["role"],
                "status": t["status"],
                "hash": hashlib.sha256(t["text"].encode()).hexdigest(),
            }
            for t in turns
        ],
        "complete_unique": "passed"
        if turns and len({t["id"] for t in turns}) == len(turns) and all(t["status"] == "completed" for t in turns)
        else "failed",
    }


def continuity(turns: list[dict]) -> str:
    expected = (
        ("user", "4172"),
        ("user", "lucky number"),
        ("assistant", "4172"),
        ("user", "multiply"),
        ("assistant", "8344"),
    )
    index = 0
    for turn in turns:
        role, phrase = expected[index]
        if turn["role"] == role and phrase in turn["text"].lower() and turn["status"] == "completed":
            index += 1
            if index == len(expected):
                return "passed"
    return "failed"


def weather_result(tools: list[dict], spoken: float | None) -> dict:
    for tool in tools:
        result = tool.get("result") or {}
        if not isinstance(result, dict) or tool["name"] != "get_current_weather":
            continue
        current = result.get("current")
        temperature = current.get("temperature_2m") if isinstance(current, dict) else None
        if (
            result.get("city") == "Tripoli"
            and result.get("country") == "Libya"
            and result.get("timezone") == "Africa/Tripoli"
            and result.get("source") == "Open-Meteo"
            and tool.get("state", "completed") == "completed"
            and isinstance(temperature, (float, int))
            and math.isfinite(temperature)
        ):
            return {
                "temperature": temperature,
                "spoken_temperature": spoken,
                "result": "pending" if spoken is None else "passed" if temperature == spoken else "failed",
            }
    return {"result": "failed"}


class Readback:
    def __init__(self, engine):
        self.engine = engine

    async def rows(self, sql: str, **params) -> list[dict]:
        # Short, fresh transactions: no idle transaction or stale snapshot across human prompts.
        async with self.engine.connect() as db, db.begin():
            await db.execute(text("SET TRANSACTION READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '5000'"))
            return [dict(row) for row in (await db.execute(text(sql), params)).mappings()]

    async def preflight(self, owner: str) -> dict:
        sessions = await self.rows("SELECT id,status FROM voice_sessions WHERE owner_id=:owner", owner=owner)
        monthly = await self.rows("""SELECT count(*) AS n FROM voice_sessions WHERE expires_at::timestamptz >=
            (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')""")
        active = await self.rows("SELECT count(*) AS n FROM voice_sessions WHERE status IN ('starting','active')")
        capabilities = await self.rows("""SELECT table_name,column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name IN ('voice_sessions','tool_calls')""")
        return {
            "observed_at": now(),
            "used": len(sessions),
            "session_ids": [s["id"] for s in sessions],
            "monthly": monthly[0]["n"],
            "active": active[0]["n"],
            "allocation": allocation(len(sessions), monthly[0]["n"], active[0]["n"]),
            "ledger": await self.rows(
                "SELECT day,provider,reserved_units FROM development_budgets ORDER BY day,provider"
            ),
            "database_revision": await self.rows("SELECT version_num FROM alembic_version"),
            "capabilities": capabilities,
        }

    async def session(self, owner: str, session: str, cleanup: bool) -> dict | None:
        column = "room_cleanup_completed_at" if cleanup else "NULL AS room_cleanup_completed_at"
        found = await self.rows(
            f"""SELECT id,conversation_id,status,{column} FROM voice_sessions
            WHERE id=:session AND owner_id=:owner""",
            session=session,
            owner=owner,
        )
        if not found:
            return None
        owned = await self.rows(
            "SELECT id FROM conversations WHERE id=:id AND owner_id=:owner", id=found[0]["conversation_id"], owner=owner
        )
        if not owned:
            raise ValueError("Conversation ownership mismatch")
        return found[0]

    async def turns(self, conversation: str) -> list[dict]:
        return await self.rows(
            """SELECT id,role,text,status FROM turns WHERE conversation_id=:conversation
            ORDER BY created_at,id""",
            conversation=conversation,
        )

    async def tools(self, conversation: str, has_state: bool) -> list[dict]:
        state = "tc.state" if has_state else "'completed' AS state"
        return await self.rows(
            f"""SELECT tc.name,tc.result,{state} FROM tool_calls tc JOIN turns t ON t.id=tc.turn_id
            WHERE t.conversation_id=:conversation""",
            conversation=conversation,
        )


def has_column(snapshot: dict, table: str, column: str) -> bool:
    return {"table_name": table, "column_name": column} in snapshot["capabilities"]


async def cleanup_readback(reader, owner: str, session: str, supported: bool, wait: float = 0) -> dict:
    started = time.monotonic()
    while True:
        observed = await reader.session(owner, session, supported)
        elapsed = time.monotonic() - started
        if observed is None:
            result = "failed"
        elif not supported:
            result = "pending"
        elif observed["status"] == "ended" and observed["room_cleanup_completed_at"]:
            result = "passed"
        elif elapsed < wait:
            await asyncio.sleep(min(1, wait - elapsed))
            continue
        else:
            result = "failed"
        return {"row": observed, "result": result, "observed_at": now(), "elapsed_seconds": elapsed}


def gates(report: dict) -> dict:
    scenarios = report["scenarios"]
    s1, s2, s3 = (scenarios.get(s, {}) for s in SCENARIOS)
    h1, h2, h3 = (s.get("human", {}) for s in (s1, s2, s3))
    get = lambda obj, key: obj.get(key, "pending")
    lifecycle = [s.get("cleanup", {}).get("result", "pending") for s in (s1, s2, s3)]
    return {
        "D1.3": aggregate(get(h1, "microphone"), get(h1, "audible"), get(h1, "reload"), get(s1, "persistence")),
        "D1.4": aggregate(
            get(h1, "clarification"), get(h1, "weather_audio"), s1.get("weather", {}).get("result", "pending")
        ),
        "D1.5": aggregate(
            get(h1, "typed_continuity"), get(h1, "voice_continuity"), get(s1, "continuity"), get(s1, "persistence")
        ),
        "D1.6": aggregate(
            *(get(h2, k) for k in QUESTIONS["S2"]),
            get(h3, "reconnect"),
            get(h3, "reload"),
            *(s.get("human", {}).get("idle", "pending") for s in (s1, s2, s3)),
            *lifecycle,
            "pending",
        ),  # Repeated-end provider-delete evidence needs coordinator review.
        "D1.7": "pending" if "after" not in report else "passed",
        "D1.8": "pending",
    }


def save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report["gates"] = gates(report)
    payload = json.dumps(report, indent=2, allow_nan=False)
    lines = ["# Assisted acceptance evidence", "", "| Gate | Result |", "|---|---|"]
    lines += [f"| {gate} | {result} |" for gate, result in report["gates"].items()]
    lines += [
        "",
        "Human confirmations are separate from readbacks. Coordinator release review remains required.",
        "",
        "```json",
        payload,
        "```",
        "",
    ]
    for target, content in ((path, payload), (path.with_suffix(".md"), "\n".join(lines))):
        # mkstemp creates private files before writing; replacement is atomic.
        fd, temporary = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(content)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)


def answer(prompt: str) -> str:
    while True:
        value = input(prompt + " [passed/failed/pending/stop]: ").strip().lower()
        if value == "stop":
            raise InterruptedError
        if value in {"passed", "failed", "pending"}:
            return value


def identifier(prompt: str) -> str:
    value = input(prompt).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value):
        raise ValueError("Invalid identifier")
    return value


def metadata() -> dict:
    return {
        key: identifier(f"{key} (ID/version only, no URLs or credentials): ")
        for key in ("source_revision", "api", "web", "worker", "browser", "os")
    }


async def scenario(reader, report: dict, label: str, path: Path) -> None:
    item = report["scenarios"].setdefault(label, {"human": {}})
    if item.get("finished"):
        if (
            item.get("persistence") != "passed"
            or any(v != "passed" for v in item["human"].values())
            or item.get("weather", {}).get("result", "passed") != "passed"
            or item.get("continuity", "passed") != "passed"
        ):
            raise InterruptedError
        return
    if (
        any(v != "passed" for v in item["human"].values())
        or item.get("cleanup", {}).get("result", "passed") != "passed"
    ):
        # Never overwrite a failed/pending observation after a repair.
        raise InterruptedError
    if "session" not in item:
        current = await reader.preflight(report["owner"])
        if "start_requested_at" in item:
            print("Resume: bind the ORIGINAL admitted session ID only. Do not start another call.")
        else:
            if not current["allocation"]:
                raise InterruptedError
            item["start_requested_at"] = now()
            save(path, report)
            print(f"{label}: follow docs/day1-acceptance-runbook.md. Start at most ONE call. Stop on any guard/error.")
            print("For S2 block microphone before starting. If denial admits no call, record it in the manual record;")
            print("the helper cannot bind a nonexistent session. Do not retry blindly.")
        session_id = identifier("Exact newly admitted session ID (not the response token): ")
        if session_id in report["before"]["session_ids"] or any(
            s.get("session") == session_id for s in report["scenarios"].values()
        ):
            raise ValueError("Old/reused session")
        session = await reader.session(report["owner"], session_id, False)
        if session is None:
            raise ValueError("Session missing or ownership mismatch")
        item.update(session=session_id, conversation=session["conversation_id"])
        save(path, report)
    for key, question in QUESTIONS[label].items():
        if key not in item["human"]:
            item["human"][key] = answer(question)
            save(path, report)
            if item["human"][key] != "passed":
                raise InterruptedError
    if label == "S1" and "spoken_temperature" not in item:
        value = float(input("Temperature actually heard, Celsius (number only): "))
        if not math.isfinite(value):
            raise ValueError("Temperature must be finite")
        item["spoken_temperature"] = value
        save(path, report)
    if "before_reload" not in item:
        item["before_reload"] = summarize_turns(await reader.turns(item["conversation"]))
        save(path, report)
    if "cleanup" not in item and "action_requested_at" in item:
        # A crash during end/refresh leaves timing uncertain. Do not replay it on resume.
        item["cleanup"] = {"result": "pending", "reason": "action interrupted; timing unverified"}
        save(path, report)
        raise InterruptedError
    if "cleanup" not in item:
        item["action_requested_at"] = now()
        save(path, report)
        print("Refresh the still-live call now." if label == "S3" else "Press End call now.")
        action_started = time.monotonic()
        item["human"]["idle"] = answer("Did the action complete and browser return to idle?")
        save(path, report)
        supported = has_column(report["before"], "voice_sessions", "room_cleanup_completed_at")
        item["cleanup"] = await cleanup_readback(
            reader,
            report["owner"],
            item["session"],
            supported,
            wait=0 if label == "S3" else max(0, 30 - (time.monotonic() - action_started)),
        )
        item["cleanup"]["elapsed_since_instruction"] = time.monotonic() - action_started
        if label != "S3" and item["cleanup"]["elapsed_since_instruction"] > 30:
            item["cleanup"]["result"] = "failed"
        save(path, report)
        if item["cleanup"]["result"] != "passed" or item["human"]["idle"] != "passed":
            raise InterruptedError
    if "reload" not in item["human"]:
        item["human"]["reload"] = answer(
            "Reload after end (S3 already refreshed). History once, ordered, tool panel retained?"
        )
        save(path, report)
    turns = await reader.turns(item["conversation"])
    after = summarize_turns(turns)
    item["after_reload"] = after
    item["persistence"] = aggregate(
        after["complete_unique"], "passed" if after["entries"] == item["before_reload"]["entries"] else "failed"
    )
    if label == "S1":
        item["continuity"] = continuity(turns)
        tools = await reader.tools(item["conversation"], has_column(report["before"], "tool_calls", "state"))
        item["weather"] = weather_result(tools, item["spoken_temperature"])
    item["finished"] = True
    save(path, report)
    if (
        item["persistence"] != "passed"
        or item.get("weather", {}).get("result", "passed") != "passed"
        or item.get("continuity", "passed") != "passed"
        or any(v != "passed" for v in item["human"].values())
    ):
        raise InterruptedError


async def execute(args) -> None:
    url = os.environ.get("ACCEPTANCE_DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("Inject hosted ACCEPTANCE_DATABASE_URL with postgresql+asyncpg driver")
    path = args.report or ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") / "report.json"
    if args.mode == "resume" and args.report is None:
        raise ValueError("Resume requires --report")
    if path.suffix != ".json" or (args.mode == "run" and path.exists()):
        raise ValueError("Use a new .json report path or resume")
    report = (
        json.loads(path.read_text()) if args.mode == "resume" else {"schema": 2, "owner": args.owner, "scenarios": {}}
    )
    if report.get("owner") != args.owner or report.get("schema") != 2:
        raise ValueError("Resume owner/schema mismatch")
    engine = create_async_engine(url, hide_parameters=True, connect_args={"timeout": 10, "command_timeout": 10})
    reader = Readback(engine)
    try:
        before = await reader.preflight(args.owner)
        print(json.dumps(before, indent=2))
        if args.mode == "preflight":
            return
        print("No credentials, URLs or free-form notes in prompts. Use headphones and real microphone.")
        revisions = metadata()
        if "revisions" in report and (
            report["revisions"] != revisions or report["before"]["database_revision"] != before["database_revision"]
        ):
            raise ValueError("Deployment changed: use a separate report")
        report.update(revisions=revisions, input_mode="human_microphone + typed_with_audio")
        report.setdefault("before", before)
        report.setdefault("plan", before["allocation"])
        save(path, report)
        print(f"Report: {path}")
        try:
            for label in report["plan"]:
                await scenario(reader, report, label, path)
        except (InterruptedError, EOFError, KeyboardInterrupt):
            report["stopped_at"] = now()
        finally:
            # Save partial observations even when the final readback is unavailable.
            report.pop("after", None)
            save(path, report)
            report["after"] = await reader.preflight(args.owner)
            report["ledger_delta"] = ledger_delta(report["before"]["ledger"], report["after"]["ledger"])
            report["reservations_consumed"] = report["after"]["used"] - report["before"]["used"]
            save(path, report)
        print(f"Evidence: {path}. No issue closure or release marker performed.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "run", "resume"))
    parser.add_argument("--owner", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(execute(args))
    except (Exception, KeyboardInterrupt):
        # Do not print exception strings: DB/driver errors may contain connection credentials.
        print(
            "Stopped: invalid input, changed deployment or unavailable readback. Check configuration without printing secrets."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
