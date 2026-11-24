"""CPU profile of POST /internal/agent/turn/start on a realistic conversation.

Seeds a throwaway SQLite database with a long call (turns with large stored contexts, many memories, one
exclusion) and runs the handler in-process under cProfile, so the report shows the Python work per turn
separately from the time a real database round trip costs (measured elsewhere). Nothing leaves the machine.

    uv run python scripts/profile_turn.py --turns 100 --memories 200 --runs 30
"""

import argparse
import asyncio
import cProfile
import io
import json
import pstats
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import httpx  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

HEADERS = {"x-agent-secret": "profile-secret", "x-session-id": "session", "x-room-name": "room"}


def big_snapshot(index: int, kilobytes: int) -> dict:
    """A stored context the size a real turn leaves behind: the prompt appears three times in it."""
    messages = [
        {"role": "user" if n % 2 else "assistant", "content": f"turn {index} message {n} " + "word " * 40}
        for n in range(max(4, kilobytes * 1024 // 3 // 260))
    ]
    return {
        "included_record_ids": [f"mem-{n}" for n in range(10)],
        "messages": messages,
        "model_request": {"messages": messages, "instructions": "Rules " * 100, "selection": messages},
        "weather_policy": {"allowed": False},
    }


def seed(path: Path, chat: str, *, turns: int, memories: int, snapshot_kb: int) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        owner = db.execute("SELECT owner_id FROM conversations WHERE id=?", (chat,)).fetchone()[0]
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
            "VALUES (?,?,?,?,?,?,?)",
            ("session", chat, owner, "room", "active", (datetime.now(UTC) + timedelta(hours=1)).isoformat(), "{}"),
        )
        for n in range(memories):
            stamp = f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}+00:00"
            db.execute(
                "INSERT INTO memories (id,owner_id,content,embedding_model,source_conversation_id,version,status,"
                "created_at,updated_at) VALUES (?,?,?, 'text-embedding-3-small', ?, 1, 'active', ?, ?)",
                (f"mem-{n}", owner, f"The user likes thing number {n} and says so often.", chat, stamp, stamp),
            )
        for n in range(turns):
            stamp = f"2026-01-02T{n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}+00:00"
            db.execute(
                "INSERT INTO turns (id,conversation_id,request_id,role,text,status,created_at,snapshot) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"old-{n}",
                    chat,
                    f"old-req-{n}",
                    "user" if n % 2 == 0 else "assistant",
                    f"History line {n}: " + "some words " * 12,
                    "completed",
                    stamp,
                    json.dumps(big_snapshot(n, snapshot_kb)),
                ),
            )


PHASES: dict[str, float] = {}


def timed(module, name: str, label: str | None = None) -> None:
    """Wrap one function so its wall time accumulates under a label (sync or async)."""
    original = getattr(module, name)
    label = label or name

    if asyncio.iscoroutinefunction(original):

        async def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                PHASES[label] = PHASES.get(label, 0.0) + (time.perf_counter() - started) * 1000

    else:

        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                PHASES[label] = PHASES.get(label, 0.0) + (time.perf_counter() - started) * 1000

    setattr(module, name, wrapper)


def instrument() -> None:
    """Phase timers for the turn-start handler, with no profiler overhead."""
    import fastapi.routing as fastapi_routing
    from app.routes import worker

    for name in (
        "read_context_inputs",
        "_compile_turn_context",
        "validate_assembly",
        "record_proposal",
        "_locked_turn_context",
        "_model_request_memories",
        "_context_instructions",
    ):
        if hasattr(worker, name):
            timed(worker, name)
    timed(fastapi_routing, "jsonable_encoder", "response: jsonable_encoder")
    original_request = worker.ModelRequest

    class TimedModelRequest(original_request):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            started = time.perf_counter()
            super().__init__(*args, **kwargs)
            PHASES["ModelRequest(...) validation"] = (
                PHASES.get("ModelRequest(...) validation", 0.0) + (time.perf_counter() - started) * 1000
            )

    worker.ModelRequest = TimedModelRequest


def worker_cost(response) -> None:
    """What the voice worker spends in Python on the same response, per turn (no network)."""
    sys.path.insert(0, str(ROOT / "services" / "voice-agent"))
    from snapshot_request import snapshot_request

    body = response.json()
    samples: dict[str, list[float]] = {}

    def measure(label: str, call) -> None:
        for _ in range(25):
            started = time.perf_counter()
            call()
            samples.setdefault(label, []).append((time.perf_counter() - started) * 1000)

    measure("decode the response JSON", lambda: json.loads(response.content))
    measure("snapshot_request (validate+hash)", lambda: snapshot_request(body["context"], body["turn_id"]))
    request = snapshot_request(body["context"], body["turn_id"])
    measure(
        "serialize request for the bounds check",
        lambda: json.dumps(
            {k: v for k, v in request.model_dump(mode="json").items() if k in {"messages", "tool_history", "tools"}},
            ensure_ascii=False,
        ).encode(),
    )
    print("\nworker Python per turn (ms, median of 25):")
    for label, values in samples.items():
        values.sort()
        print(f"  {label:38s} {values[len(values) // 2]:7.2f}")


async def main(args) -> None:
    if not args.profile:
        instrument()
    folder = Path(tempfile.mkdtemp(prefix="profile-turn-"))
    database = folder / "profile.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database}",
        agent_service_secret="profile-secret",
        context_proposal_secret="proposal-secret",
        _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            await client.post("/v1/demo-session")
            chat = (await client.post("/v1/conversations")).json()["id"]
            seed(database, chat, turns=args.turns, memories=args.memories, snapshot_kb=args.snapshot_kb)
            for warm in range(2):  # imports, caches
                await client.post(
                    "/internal/agent/turn/start", headers=HEADERS, json={"request_id": f"w{warm}", "text": "hi"}
                )
            profile, wall = cProfile.Profile(), []
            for run in range(args.runs):
                started = time.perf_counter()
                if args.profile:
                    profile.enable()
                response = await client.post(
                    "/internal/agent/turn/start",
                    headers=HEADERS,
                    json={"request_id": f"r{run}", "text": "What is the weather like?"},
                )
                if args.profile:
                    profile.disable()
                wall.append((time.perf_counter() - started) * 1000)
                assert response.status_code == 200, response.text
                size = len(response.content)
                last = response
    worker_cost(last)
    wall.sort()
    print(
        f"turn/start in-process: p50 {wall[len(wall) // 2]:.1f} ms, p95 {wall[int(len(wall) * 0.95)]:.1f} ms, "
        f"max {wall[-1]:.1f} ms over {len(wall)} runs; response {size / 1024:.1f} KB (SQLite, no network)"
    )
    if not args.profile:
        print("\nwhere the time goes per turn (ms, mean over the runs; nested phases overlap):")
        for label, total in sorted(PHASES.items(), key=lambda item: -item[1]):
            print(f"  {label:34s} {total / len(wall):7.2f}")
        return
    # cProfile inflates code made of many tiny calls (the JSON encoder, ORM attribute access) several times
    # over: read the percentages as relative cost, and take milliseconds from the unprofiled run above.
    for order, title in (("tottime", "own time"), ("cumulative", "cumulative time")):
        out = io.StringIO()
        stats = pstats.Stats(profile, stream=out).sort_stats(order)
        stats.print_stats(args.top)
        print(f"\n== top {args.top} by {title} ==")
        print("\n".join(line for line in out.getvalue().splitlines() if line.strip())[:6000])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=100)
    parser.add_argument("--memories", type=int, default=200)
    parser.add_argument("--snapshot-kb", type=int, default=30)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--no-profile", dest="profile", action="store_false", help="timings only, no profiler")
    asyncio.run(main(parser.parse_args()))
