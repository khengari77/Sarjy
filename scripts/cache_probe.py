"""Manual DeepSeek prompt-cache and TTFT probe for simulated chat turns.

This is a paid, manual tool: it is never run by CI and only makes provider calls
with ``--live``.  It drives the real API in-process against a throwaway SQLite
database, streams each turn's production-shaped request to DeepSeek, and writes
one JSONL row per turn.  ``report`` is offline and reads only the JSONL.

    uv run python scripts/cache_probe.py run
    uv run python scripts/cache_probe.py run --live --out artifacts/cache-probe.jsonl
    uv run python scripts/cache_probe.py report artifacts/cache-probe.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
for _candidate in (ROOT / "apps" / "api", ROOT / "services" / "voice-agent"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

PROMPT_URL = "https://api.deepseek.com/v1/chat/completions"
# provider_factory.build_llm uses this model for deepseek_direct.
MODEL = "deepseek-flash"
DEFAULT_OUT = Path("artifacts/cache-probe.jsonl")
SERVICE_SECRET = "cache-probe-service-secret"
PROPOSAL_SECRET = "cache-probe-proposal-secret"
ALIGNMENT = 256
MAX_CONSECUTIVE_HTTP_ERRORS = 3
BUCKET_ORDER = ("1", "2-3", "4-10", "overall")

# Rough offline planning only; real prompt size comes from the compiled turns.
ROUGH_BASE_PROMPT_TOKENS = 1400
ROUGH_EXCHANGE_TOKENS = 100
ROUGH_COMPLETION_TOKENS = 60
# DeepSeek cache-miss input and output list prices, in USD per million tokens.
ROUGH_INPUT_USD_PER_MTOK = 0.28
ROUGH_OUTPUT_USD_PER_MTOK = 0.42

# Chat-only, 8-20 words each: no memory facts, weather, settings commands or Wordle.
PHRASES: tuple[str, ...] = (
    "I have been reading a lot about ancient cities and how they were planned.",
    "What do you think makes a good conversation last for hours?",
    "My neighbor is building a small greenhouse in his backyard this summer.",
    "Can you explain why some songs stay stuck in your head?",
    "We spent the afternoon walking along the river and talking about old films.",
    "I never expected the museum to be so crowded on a weekday.",
    "Tell me something interesting about the history of paper making.",
    "The train was late again, so I finished my book at the station.",
    "How would you describe the difference between curiosity and patience?",
    "My cousin is learning to cook and keeps burning the garlic.",
    "There is a small bakery near my office that opens very early.",
    "Do you think people read fewer books now than before?",
    "I watched a documentary about deep sea creatures last night.",
    "Our discussion about education went much longer than we planned.",
    "What small habit has made the biggest difference in your day?",
    "The old library downtown is being repaired for the first time in decades.",
    "I am trying to decide whether to take a pottery class this autumn.",
    "Why do some stories feel familiar even when we hear them once?",
    "My friend moved to a coastal town and sends photographs every week.",
    "Can you suggest a few questions to ask someone I just met?",
    "We argued about the ending of that novel for the whole evening.",
    "I find it easier to think clearly when I walk without music.",
    "The market was full of spices and the smell followed us home.",
    "How do you usually remember the names of people you meet?",
    "My brother repairs old radios as a hobby on weekends.",
    "There was a power cut, so we talked by candlelight for hours.",
    "What makes a city feel welcoming to someone who just arrived?",
    "I have started writing short notes about things I notice daily.",
    "The teacher told a story about her first year in the classroom.",
    "Do you believe that practice matters more than natural talent?",
    "We found an old map in the attic and tried to follow it.",
    "My favorite part of the day is the quiet hour after sunset.",
    "Can you think of a simple way to explain how tides work?",
    "The concert ended late and we missed the last bus home.",
    "I keep a small notebook for ideas that arrive while I walk.",
    "What would you ask a traveler who has visited many countries?",
    "Our neighbor's cat sits on the fence and watches everyone pass.",
    "I am curious about how people chose their favorite colors.",
    "The workshop taught us to repair furniture instead of replacing it.",
    "Why does a familiar place look different after a long absence?",
)


class ProbeTurnError(Exception):
    """A provider or stream failure; the next turn may still run."""


class ProbeFatal(Exception):
    """A local API failure that makes the remaining measurements meaningless."""


def opening_phrase(phrase: str, nonce: str) -> str:
    """The first user message of a conversation, made unique so no earlier run or conversation can have cached it.

    DeepSeek caches every request prefix it has seen, so a repeated opening would hit the cache of a previous run and
    inflate the hit rate. The nonce sits after the shared system prompt and tools (which production also shares) and
    changes every following byte of the conversation.
    """
    return f"(call {nonce}) {phrase}"


def serialize_messages(messages: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(list(messages), ensure_ascii=False, separators=(",", ":"))


def aligned_common_prefix(previous: str | None, current: str) -> int:
    """Common prefix length with ``previous``, rounded down to ``ALIGNMENT`` characters."""
    if not previous:
        return 0
    limit = min(len(previous), len(current))
    index = 0
    while index < limit and previous[index] == current[index]:
        index += 1
    return (index // ALIGNMENT) * ALIGNMENT


def turn_bucket(turn: int) -> str:
    if turn <= 1:
        return "1"
    if turn <= 3:
        return "2-3"
    return "4-10"


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _bucket_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    hits = sum(_as_int(row.get("hit")) for row in rows)
    misses = sum(_as_int(row.get("miss")) for row in rows)
    prompt_tokens = sum(_as_int(row.get("prompt_tokens")) for row in rows)
    ttft = [
        float(value)
        for row in rows
        if isinstance(value := row.get("ttft_ms"), (int, float)) and not isinstance(value, bool)
    ]
    return {
        "n": len(rows),
        "hit_pct": 100.0 * hits / (hits + misses) if hits + misses else None,
        "mean_miss_tokens": misses / len(rows) if rows else None,
        "mean_prompt_tokens": prompt_tokens / len(rows) if rows else None,
        "ttft_p50": percentile(ttft, 0.50),
        "ttft_p95": percentile(ttft, 0.95),
    }


def summarize_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    valid = [row for row in rows if isinstance(row.get("turn"), int) and not isinstance(row.get("turn"), bool)]
    grouped: dict[str, list[Mapping[str, Any]]] = {bucket: [] for bucket in BUCKET_ORDER[:3]}
    for row in valid:
        grouped[turn_bucket(int(row["turn"]))].append(row)
    grouped["overall"] = valid
    return {name: _bucket_stats(values) for name, values in grouped.items()}


def estimate_plan(conversations: int, turns: int) -> dict[str, int]:
    calls = conversations * turns
    prior_exchanges = (turns - 1) / 2
    prompt_tokens = round(calls * (ROUGH_BASE_PROMPT_TOKENS + prior_exchanges * ROUGH_EXCHANGE_TOKENS))
    completion_tokens = calls * ROUGH_COMPLETION_TOKENS
    return {
        "calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """All-cache-miss upper bound; cached input tokens are billed lower."""
    return (
        prompt_tokens / 1_000_000 * ROUGH_INPUT_USD_PER_MTOK + completion_tokens / 1_000_000 * ROUGH_OUTPUT_USD_PER_MTOK
    )


class JsonlSink:
    """Write each row immediately so an interrupted run keeps its evidence."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("x", encoding="utf-8", buffering=1)

    def write(self, row: Mapping[str, Any]) -> None:
        self._file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class _FakeEmbedder:
    """Memory is off, so this is never called; it only keeps create_app local."""

    async def embed(self, db: Any, text: str) -> tuple[float, ...]:
        return (1.0, *([0.0] * 1535))


def _settings(database: Path) -> Any:
    from app.config import Settings

    return Settings(
        database_url=f"sqlite+aiosqlite:///{database}",
        agent_service_secret=SERVICE_SECRET,
        context_proposal_secret=PROPOSAL_SECRET,
        # The probe streams directly; never let the API make its own provider calls.
        deepseek_api_key="",
        openai_api_key="",
        jev_api_key="",
        deepseek_budget_enabled=False,
        wordle_enabled=False,
        development_protected=False,
        reviewer_access_enabled=False,
        account_access_enabled=False,
        _env_file=None,
    )


def _set_memory_off(database: Path, owner_id: str) -> None:
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT OR REPLACE INTO user_settings (owner_id,speaking_speed,captions_visible,response_preference,"
            "memory_mode,revision,updated_at) VALUES (?,?,?,?,?,?,?)",
            (owner_id, 1.0, 1, "automatic", "off", 1, "2026-11-25T00:00:00+00:00"),
        )
        db.commit()


def _add_turn_session(database: Path, conversation_id: str, owner_id: str, identifier: str) -> dict[str, str]:
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                identifier,
                conversation_id,
                owner_id,
                f"room-{identifier}",
                "active",
                (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                json.dumps({}),
            ),
        )
        db.commit()
    return {
        "x-agent-secret": SERVICE_SECRET,
        "x-session-id": identifier,
        "x-room-name": f"room-{identifier}",
    }


def _worker_tools() -> list[Any]:
    """The same tool objects the worker offers when the Wordle popup is closed."""
    from main import SarjyAgent

    agent = SarjyAgent(None, {"llm": {"provider": "deepseek_direct"}}, "Rules")
    return list(agent._wordle_tools(agent.tools))


def _provider_messages(model_request: Mapping[str, Any]) -> list[dict[str, Any]]:
    from app.domain.contracts import ModelRequest
    from generation import _build_context

    request = ModelRequest.model_validate(dict(model_request))
    context = _build_context(request)
    messages, _extra = context.to_provider_format(format="openai")
    return [dict(message) for message in messages]


def _tool_schemas(tools: Sequence[Any]) -> list[dict[str, Any]]:
    from livekit.agents import llm

    return list(llm.ToolContext(list(tools)).parse_function_tools("openai"))


def build_payload(model_request: Mapping[str, Any], tools: Sequence[Any], *, model: str = MODEL) -> dict[str, Any]:
    """The production wire body: provider messages, strict tools, cap and thinking off."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": _provider_messages(model_request),
        "max_tokens": int(model_request["response_policy"]["max_output_tokens"]),
        "stream": True,
        "stream_options": {"include_usage": True},
        "thinking": {"type": "disabled"},
    }
    schemas = _tool_schemas(tools)
    if schemas:
        payload["tools"] = schemas
    return payload


def _error_label(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http {exc.response.status_code}"
    return type(exc).__name__


def _usage_fields(usage: Mapping[str, Any]) -> tuple[int, int, int, int]:
    hit = _as_int(usage.get("prompt_cache_hit_tokens"))
    miss = _as_int(usage.get("prompt_cache_miss_tokens"))
    prompt = _as_int(usage.get("prompt_tokens")) or hit + miss
    completion = _as_int(usage.get("completion_tokens"))
    return hit, miss, prompt, completion


def _stream_completion(
    http: httpx.Client, api_key: str, payload: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any], float | None, float]:
    started = time.perf_counter()
    first_content_at: float | None = None
    parts: list[str] = []
    usage: Mapping[str, Any] | None = None
    with http.stream(
        "POST",
        PROMPT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=dict(payload),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data or data == "[DONE]":
                continue
            chunk = json.loads(data)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                content = (choice.get("delta") or {}).get("content")
                if content:
                    if first_content_at is None:
                        first_content_at = time.perf_counter()
                    parts.append(content)
    finished = time.perf_counter()
    if usage is None:
        raise ValueError("MISSING_USAGE")
    ttft_ms = None if first_content_at is None else (first_content_at - started) * 1000
    return "".join(parts).strip(), usage, ttft_ms, (finished - started) * 1000


def _run_turn(
    client: Any,
    http: httpx.Client,
    api_key: str,
    headers: Mapping[str, str],
    tools: Sequence[Any],
    *,
    conversation: int,
    turn: int,
    phrase: str,
    previous_prompt: str | None,
) -> tuple[dict[str, Any], str]:
    request_id = f"cache-probe-{conversation}-{turn}-{uuid4().hex}"
    try:
        started = client.post(
            "/internal/agent/turn/start",
            headers=dict(headers),
            json={"request_id": request_id, "text": phrase, "wordle_open": False},
        )
        started.raise_for_status()
        body = started.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProbeFatal(f"turn/start failed: {type(exc).__name__}") from exc

    turn_id = body["turn_id"]
    model_request = body["context"]["model_request"]
    payload = build_payload(model_request, tools)
    current_prompt = serialize_messages(payload["messages"])
    try:
        reply, usage, ttft_ms, total_ms = _stream_completion(http, api_key, payload)
    except (httpx.HTTPError, ValueError) as exc:
        raise ProbeTurnError(f"provider failed: {_error_label(exc)}") from exc

    try:
        finalized = client.post(
            "/internal/agent/turn/finalize",
            headers=dict(headers),
            json={"turn_id": turn_id, "text": reply, "status": "completed"},
        )
        finalized.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProbeFatal(f"turn/finalize failed: {type(exc).__name__}") from exc

    hit, miss, prompt_tokens, completion_tokens = _usage_fields(usage)
    row = {
        "conversation": conversation,
        "turn": turn,
        "text": phrase,
        "prompt_tokens": prompt_tokens,
        "hit": hit,
        "miss": miss,
        "completion_tokens": completion_tokens,
        "ttft_ms": None if ttft_ms is None else round(ttft_ms, 1),
        "total_ms": round(total_ms, 1),
        "prompt_chars": len(current_prompt),
        "prefix_chars": aligned_common_prefix(previous_prompt, current_prompt),
        "tools_count": len(payload.get("tools") or []),
        "model": MODEL,
    }
    return row, current_prompt


def _run_probe(
    client: Any,
    http: httpx.Client,
    api_key: str,
    database: Path,
    tools: Sequence[Any],
    args: argparse.Namespace,
    sink: JsonlSink,
) -> int:
    owner = client.post("/v1/demo-session").json()["id"]
    _set_memory_off(database, owner)
    consecutive_errors = 0
    phrase_index = args.phrase_offset
    for conversation in range(1, args.conversations + 1):
        chat = client.post("/v1/conversations").json()["id"]
        headers = _add_turn_session(database, chat, owner, f"cache-probe-{conversation}")
        previous_prompt: str | None = None
        nonce = uuid4().hex[:10]
        for turn in range(1, args.turns + 1):
            phrase = PHRASES[phrase_index % len(PHRASES)]
            phrase_index += 1
            if turn == 1:
                phrase = opening_phrase(phrase, nonce)
            try:
                row, previous_prompt = _run_turn(
                    client,
                    http,
                    api_key,
                    headers,
                    tools,
                    conversation=conversation,
                    turn=turn,
                    phrase=phrase,
                    previous_prompt=previous_prompt,
                )
            except ProbeTurnError as exc:
                consecutive_errors += 1
                print(f"conversation {conversation} turn {turn}: {exc}", file=sys.stderr)
                if consecutive_errors >= MAX_CONSECUTIVE_HTTP_ERRORS:
                    print(
                        f"aborted after {MAX_CONSECUTIVE_HTTP_ERRORS} consecutive provider errors",
                        file=sys.stderr,
                    )
                    return 3
            except ProbeFatal as exc:
                print(f"conversation {conversation} turn {turn}: {exc}", file=sys.stderr)
                return 3
            else:
                consecutive_errors = 0
                sink.write(row)
            if turn < args.turns:
                time.sleep(args.pause_seconds)
        if conversation < args.conversations:
            time.sleep(args.conversation_pause_seconds)
    return 0


def _run_live(args: argparse.Namespace) -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2
    try:
        sink = JsonlSink(args.out)
    except FileExistsError:
        print(f"{args.out} already exists; refusing to overwrite", file=sys.stderr)
        return 2
    with sink, tempfile.TemporaryDirectory(prefix="sarjy-cache-probe-") as temp:
        database = Path(temp) / "probe.db"
        from app.main import create_app
        from fastapi.testclient import TestClient

        with TestClient(create_app(_settings(database), embedding_adapter=_FakeEmbedder())) as client:
            tools = _worker_tools()
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as http:
                return _run_probe(client, http, api_key, database, tools, args, sink)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(parsed, dict):
                skipped += 1
                continue
            rows.append(parsed)
    if skipped:
        print(f"skipped {skipped} unparsable lines in {path}", file=sys.stderr)
    return rows


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


_HEADERS = ("bucket", "n", "hit%", "mean_miss", "mean_prompt", "ttft_p50", "ttft_p95")
_WIDTHS = (8, 5, 9, 10, 12, 10, 10)


def _print_summary(summary: Mapping[str, Mapping[str, Any]]) -> None:
    print(" ".join(f"{heading:>{width}}" for heading, width in zip(_HEADERS, _WIDTHS, strict=True)))
    for name in BUCKET_ORDER:
        stats = summary[name]
        cells = (
            name,
            str(stats["n"]),
            _fmt(stats["hit_pct"]),
            _fmt(stats["mean_miss_tokens"]),
            _fmt(stats["mean_prompt_tokens"]),
            _fmt(stats["ttft_p50"]),
            _fmt(stats["ttft_p95"]),
        )
        print(" ".join(f"{cell:>{width}}" for cell, width in zip(cells, _WIDTHS, strict=True)))


def report(path: Path) -> int:
    try:
        rows = _read_rows(path)
    except OSError as exc:
        print(f"cannot read {path}: {type(exc).__name__}", file=sys.stderr)
        return 2
    summary = summarize_rows(rows)
    if not summary["overall"]["n"]:
        print(f"no usable rows in {path}", file=sys.stderr)
        return 2
    _print_summary(summary)
    return 0


def print_plan(args: argparse.Namespace) -> int:
    plan = estimate_plan(args.conversations, args.turns)
    cost = estimate_cost_usd(plan["prompt_tokens"], plan["completion_tokens"])
    print("cache probe plan (offline; no network calls will be made)")
    print(f"  conversations: {args.conversations}, turns each: {args.turns}")
    print(f"  provider calls: {plan['calls']}")
    print(f"  rough prompt tokens: {plan['prompt_tokens']}")
    print(f"  rough completion tokens: {plan['completion_tokens']}")
    print(f"  rough cost if every prompt token is a cache miss: ${cost:.4f}")
    print(f"  output: {args.out}")
    print("run again with --live to execute")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepSeek prompt-cache and TTFT probe")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="measure N conversations of T chat turns")
    run_parser.add_argument("--conversations", type=int, default=10)
    run_parser.add_argument("--turns", type=int, default=10)
    run_parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run_parser.add_argument(
        "--phrase-offset", type=int, default=0, help="start in the phrase list here, so runs do not share prompts"
    )
    run_parser.add_argument("--pause-seconds", type=float, default=1.0)
    run_parser.add_argument("--conversation-pause-seconds", type=float, default=5.0)
    run_parser.add_argument("--live", action="store_true", help="allow paid provider calls")

    report_parser = commands.add_parser("report", help="print hit-rate and TTFT buckets")
    report_parser.add_argument("file", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "report":
        return report(args.file)
    if args.conversations < 1 or args.turns < 1:
        print("--conversations and --turns must be at least 1", file=sys.stderr)
        return 2
    if args.pause_seconds < 0 or args.conversation_pause_seconds < 0:
        print("pause options must not be negative", file=sys.stderr)
        return 2
    if not args.live:
        return print_plan(args)
    try:
        return _run_live(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
