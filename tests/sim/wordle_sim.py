"""Text-mode Wordle simulation against the REAL worker logic and the real DeepSeek model.

Not a pytest module (no ``test_`` prefix); it spends money, so it is gated by a
persistent game counter (hard cap) and run by hand:

    uv run python tests/sim/wordle_sim.py --games 10 --seed 1 --label before

It drives ``SarjyAgent`` (llm_node, tool forcing, tools) with the real DeepSeek
provider, the real API app (ASGI, throwaway sqlite, no ports) and the real
Wordle services, and scores every turn against the database truth.
"""

import argparse
import asyncio
import json
import random
import re
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for extra in ("apps/api", "services/voice-agent", "tests"):
    sys.path.insert(0, str(ROOT / extra))

import httpx  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from livekit.agents import llm  # noqa: E402
from main import SarjyAgent  # noqa: E402
from provider_factory import build_llm  # noqa: E402
from test_context_workflow import FakeEmbedder  # noqa: E402
from test_wordle_source import FixtureSource  # noqa: E402
from wordle_commands import command_kind  # noqa: E402

COUNTER = ROOT.parent / ".wordle-sim-games.json"
HARD_CAP = 100
ENV_FILE = Path("/home/khengari77/Programming/Sarjy/.env")
ANSWERS = ["CRANE", "SLATE", "PLANT", "GRAPE", "MOUSE", "BRICK", "STONE", "WATER", "LIGHT", "CLOUD", "PIANO", "TRAIN"]
GUESSES = ["SNAKE", "SNACK", "FALSE", "HOUSE", "SMILE", "CRANE", "SLATE", "ROAST", "PRINT", "QUICK", "AUDIO", "BLEND"]
MARK_WORDS = {"green": "green", "yellow": "yellow", "gray": "gray", "grey": "gray"}
NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def games_used() -> int:
    try:
        return int(json.loads(COUNTER.read_text())["used"])
    except (OSError, ValueError, KeyError):
        return 0


def record_game() -> int:
    import fcntl

    with open(str(COUNTER) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        used = games_used() + 1
        COUNTER.write_text(json.dumps({"used": used, "cap": HARD_CAP}))
    return used


class Handle:
    def __init__(self, ident: str):
        self.id = ident
        self.interrupted = False
        self.chat_items: list = []

    async def wait_for_playout(self):
        return None

    def exception(self):
        return None


class Intercept(httpx.AsyncBaseTransport):
    """The local development ledger is stubbed; everything else is the real API."""

    def __init__(self, inner):
        self.inner = inner

    async def handle_async_request(self, request):
        if request.url.path == "/internal/development/reserve":
            return httpx.Response(200, json={})
        return await self.inner.handle_async_request(request)


def spell(word: str, style: int) -> str:
    return (", " if style else " ").join(word) + "."


def utterance(kind: str, word: str, rng: random.Random) -> str:
    cap = word.capitalize()
    forms = {
        "plain": [f"{cap}."],
        "my_word": [
            f"My word is {word.lower()}.",
            f"No. My word my word is {word.lower()}.",
            f"I think it's {word.lower()}.",
        ],
        "spelled": [spell(word, 0), spell(word, 1), f"{spell(word, 0)} {cap}."],
        "noise": [f"{cap}, uh.", f"Uh... {word.lower()}.", f"{cap}. {cap}.", f"Um, {word.lower()}, yeah."],
        "guess_phrase": [f"Guess {word.lower()}.", f"My guess is {word.lower()}."],
        "be_quick": ["Be quick.", "Quick, quick.", "Hurry up."],
        "status": [
            "Where am I at in this game?",
            "What's my status?",
            "How many guesses left?",
            "What have I tried so far?",
        ],
        "hint": ["Give me a hint.", "Is there a vowel in it?", "Can you help me?"],
        "four_letter": [f"{cap[:4]}."],
        "single_letter": ["R.", "S.", "Okay."],
        "close": ["Close the popup.", "Okay that's enough, close it.", "Back to chat please."],
        "chat": ["Tell me a joke.", "How are you?"],
    }
    return rng.choice(forms[kind])


# Which turn kinds need a Wordle tool call when a game is open.
EXPECT_TOOL = {"plain", "my_word", "spelled", "noise", "guess_phrase", "repeat"}
EXPECT_NONE = {"be_quick", "hint", "four_letter", "single_letter", "chat"}


def script(rng: random.Random, answer: str) -> list[tuple[str, str]]:
    """One game: a believable mix of guesses and conversational noise."""
    pool = [w for w in GUESSES if w != answer]
    rng.shuffle(pool)
    turns: list[tuple[str, str]] = []
    guess_kinds = ["plain", "my_word", "spelled", "noise", "guess_phrase", "plain"]
    for index in range(4):
        turns.append((rng.choice(guess_kinds), pool[index]))
    extras = ["be_quick", "status", "hint", "four_letter", "single_letter", "chat", "close"]
    for kind in rng.sample(extras, 3):
        turns.insert(rng.randint(1, len(turns)), (kind, pool[rng.randint(0, 5)]))
    if rng.random() < 0.5:
        turns.insert(rng.randint(2, len(turns)), ("repeat", pool[0]))
    turns.append(("plain", answer))
    return turns


def claims_in(reply: str) -> dict:
    lowered = reply.lower()
    attempts = [
        NUMBERS.get(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
        for m in re.finditer(
            r"\b(\d|zero|one|two|three|four|five|six)\s+(?:guesses|attempts|tries)\s+(?:left|remaining|remain)\b",
            lowered,
        )
    ]
    marks = bool(re.search(r"\b(green|yellow|gr[ae]y|correct position|wrong position|not in the word)\b", lowered))
    pairs: list[tuple[str, str]] = []
    pending: list[str] = []
    for token in re.findall(r"[A-Za-z]+|[,.;]", reply):
        if len(token) == 1 and token.isalpha() and token.isupper():
            pending.append(token)
        elif token.lower() in MARK_WORDS:
            pairs.extend((letter, MARK_WORDS[token.lower()]) for letter in pending)
            pending = []
        elif token in {".", ";"}:
            pending = []
    return {"attempts": [a for a in attempts if a is not None], "marks": marks, "pairs": pairs}


class Game:
    def __init__(self, llm_provider, answer: str, mode: str, rng: random.Random, work: Path, deepseek_key: str):
        self.llm_provider = llm_provider
        self.answer = answer
        self.mode = mode
        self.rng = rng
        self.work = work
        self.deepseek_key = deepseek_key
        self.active = mode == "preopen"
        self.turn_log: list[dict] = []
        self.usage = Counter()
        self.handle: Handle | None = None
        self.forced_log: list[dict] = []

    def active_now(self) -> bool:
        return self.active

    async def run(self) -> list[dict]:
        db = self.work / "sim.db"
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{db}",
            agent_service_secret="sim-secret",
            context_proposal_secret="sim-proposal",
            wordle_enabled=True,
            deepseek_api_key=self.deepseek_key,
            _env_file=None,
        )
        app = create_app(
            settings, embedding_adapter=FakeEmbedder(), wordle_source=FixtureSource(default_answer=self.answer)
        )
        async with app.router.lifespan_context(app):
            web = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test")
            owner = (await web.post("/v1/demo-session")).json()["id"]
            chat = (await web.post("/v1/conversations")).json()["id"]
            pipeline = {"llm": {"provider": "deepseek_direct", "model": "deepseek-flash"}, "language": "en"}
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO voice_sessions (id,conversation_id,owner_id,room_name,status,expires_at,pipeline)"
                    " VALUES (?,?,?,?,?,?,?)",
                    ("session", chat, owner, "room", "active", "2999-01-01T00:00:00+00:00", json.dumps(pipeline)),
                )
            headers = {"x-agent-secret": "sim-secret", "x-session-id": "session", "x-room-name": "room"}
            backend = httpx.AsyncClient(
                transport=Intercept(httpx.ASGITransport(app)), base_url="http://test", headers=headers
            )
            boot = (await backend.post("/internal/agent/bootstrap")).json()
            await self.drive(web, backend, boot)
            await web.aclose()
            await backend.aclose()
        return self.turn_log

    async def truth(self, web) -> dict:
        body = (await web.get("/v1/wordle/today")).json()
        game = body.get("game") or {}
        return {
            "status": game.get("status"),
            "remaining": game.get("attempts_remaining"),
            "guesses": [(g["word"], g["marks"]) for g in game.get("guesses", [])],
        }

    async def publish_wordle(self, action: str) -> None:
        self.active = action in {"open", "refresh"} or (action != "close" and self.active)
        if action == "close":
            self.active = False

    async def publish_command(self, _command) -> None:
        return None

    async def drive(self, web, backend, boot) -> None:
        game = self
        agent = SarjyAgent(
            backend,
            boot["pipeline"],
            instructions=boot["instructions"],
            publish=self.publish_command,
            publish_wordle=self.publish_wordle,
            wordle_active=self.active_now,
            is_command=lambda text: command_kind(text, wordle_active=game.active) is not None,
        )
        SarjyAgent.session = property(lambda _agent: SimpleNamespace(current_speech=game.handle, llm=game.llm_provider))
        original = agent._forced_wordle_choice if hasattr(agent, "_forced_wordle_choice") else None
        if original is not None:

            def logged(user, available):
                forced = original(user, available)
                self.forced_log.append({"turn": user.id, "forced": forced is not None, "active": self.active})
                return forced

            agent._forced_wordle_choice = logged
        direct_log: list[str] = []
        real_direct = agent._wordle_direct

        async def logged_direct(path, **arguments):
            result = await real_direct(path, **arguments)
            direct_log.append(path)
            return result

        agent._wordle_direct = logged_direct
        self.direct_log = direct_log
        ctx = llm.ChatContext()
        turns = script(self.rng, self.answer)
        if self.mode in {"normal", "late"}:
            turns.insert(0, ("play", ""))
        late_left = 2 if self.mode == "late" else 0
        for index, (kind, word) in enumerate(turns):
            if kind == "play":
                text = "Can we play the daily Wordle?"
            elif kind == "repeat":
                prior = (await self.truth(web))["guesses"]
                word = prior[0][0] if prior else word
                text = utterance("plain", word, self.rng)
            else:
                text = utterance(kind, word, self.rng)
            if self.mode == "late" and kind != "play" and late_left == 0 and not self.active and index > 0:
                self.active = True
            if self.mode == "late" and kind != "play" and late_left > 0 and kind in EXPECT_TOOL:
                late_left -= 1
            await self.turn(agent, ctx, web, kind, text, index)

    async def turn(self, agent, ctx, web, kind: str, text: str, index: int) -> None:
        before = await self.truth(web)
        was_active = self.active
        message = llm.ChatMessage(id=f"m{index}", role="user", content=[text])
        ctx.items.append(message)
        await agent.on_user_turn_completed(ctx, message)
        record = {
            "i": index,
            "kind": kind,
            "text": text,
            "active": was_active,
            "tools": [],
            "forced": False,
            "fast_path": False,
            "reply": "",
            "error": None,
        }
        if message.id in agent.suppressed:
            record["fast_path"] = True
            agent.suppressed.discard(message.id)
            await self.client_fast_path(web, text)
            record["after"] = await self.truth(web)
            self.turn_log.append(record)
            return
        self.handle = Handle(f"h{index}")
        direct_before = len(self.direct_log)
        replies: list[str] = []
        try:
            await self.passes(agent, ctx, record, replies)
        except Exception as exc:  # noqa: BLE001 - recorded, the run continues
            record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        reply = " ".join(replies).strip()
        record["reply"] = reply
        record["direct"] = self.direct_log[direct_before:]
        forced = [entry for entry in self.forced_log if entry["turn"] == message.id]
        record["forced"] = any(entry["forced"] for entry in forced)
        record["after"] = await self.truth(web)
        record["before"] = before
        self.handle.chat_items = [llm.ChatMessage(role="assistant", content=[reply])]
        ctx.items.append(llm.ChatMessage(role="assistant", content=[reply]))
        if agent.pending:
            await asyncio.gather(*agent.pending)
        self.turn_log.append(record)

    async def passes(self, agent, ctx, record, replies: list[str]) -> None:
        """Emulate AgentSession: run llm_node, execute the tool calls it yields, continue until text."""
        for _ in range(4):
            known = {tool.info.name for tool in agent._wordle_tools(agent.tools)}
            text_parts: list[str] = []
            calls: list = []
            async for chunk in agent.llm_node(ctx, agent.tools, None):
                if chunk.usage:
                    self.usage["prompt"] += chunk.usage.prompt_tokens
                    self.usage["completion"] += chunk.usage.completion_tokens
                if chunk.delta and chunk.delta.content:
                    text_parts.append(chunk.delta.content)
                if chunk.delta and chunk.delta.tool_calls:
                    calls.extend(chunk.delta.tool_calls)
            if text_parts:
                replies.append("".join(text_parts))
            if not calls:
                return
            for call in calls:
                record["tools"].append(call.name)
                ctx.items.append(llm.FunctionCall(call_id=call.call_id, name=call.name, arguments=call.arguments))
                if call.name == "play_wordle" and call.name not in known:
                    # LiveKit answers a call to a tool outside the offered list with an error.
                    output, failed = "unknown tool", True
                else:
                    stub = SimpleNamespace(function_call=SimpleNamespace(call_id=call.call_id))
                    output = str(
                        await getattr(agent, call.name)._func(agent, stub, **json.loads(call.arguments or "{}"))
                    )
                    failed = False
                record.setdefault("tool_outputs", []).append(output)
                ctx.items.append(
                    llm.FunctionCallOutput(call_id=call.call_id, name=call.name, output=output, is_error=failed)
                )

    async def client_fast_path(self, web, text: str) -> None:
        """The web client acts on a worker-suppressed turn: open on play, guess on a guess command."""
        kind = command_kind(text, wordle_active=True)
        if kind == "play":
            self.active = True
        elif kind == "quit":
            self.active = False
        elif kind == "guess":
            letters = re.sub(r"[^a-z]", "", re.sub(r"^.*?(?:guess is|guess)\s*", "", text.lower()))
            if len(letters) == 5:
                await web.post(
                    "/v1/wordle/guesses",
                    json={"guess": letters.upper(), "request_id": f"fp-{letters}-{time.time_ns()}"},
                )


def score(record: dict, answer: str) -> dict:
    """Return the failure flags for one recorded turn."""
    flags: dict[str, bool] = {}
    if record["fast_path"]:
        return flags
    kind = record["kind"]
    wordle_tools = [
        name for name in record["tools"] if name.endswith("wordle") or name.startswith(("submit_", "wordle_"))
    ]
    wordle_tools += record.get("direct", [])
    reply = record["reply"]
    after = record.get("after") or {}
    claims = claims_in(reply)
    open_game = record["active"]
    if open_game and kind in EXPECT_TOOL:
        flags["no_tool_for_guess"] = "submit_wordle_guess" not in record["tools"] and "wordle-guess" not in record.get(
            "direct", []
        )
        flags["guess_not_recorded"] = (
            ("submit_wordle_guess" in record["tools"] or "wordle-guess" in record.get("direct", []))
            and len(after.get("guesses", [])) <= len((record.get("before") or {}).get("guesses", []))
            and kind != "repeat"
        )
    if open_game and kind in EXPECT_NONE:
        flags["over_tool"] = "submit_wordle_guess" in record["tools"]
    if kind == "play":
        flags["no_play_tool"] = "play_wordle" not in record["tools"]
    elif "play_wordle" in record["tools"]:
        flags["play_while_open"] = open_game
    if kind == "close":
        flags["close_not_called"] = (
            open_game and "close_wordle" not in record["tools"] and "wordle-close" not in record.get("direct", [])
        )
    unsupported = not wordle_tools and (claims["marks"] or bool(claims["attempts"]))
    flags["unsupported_claim"] = bool(unsupported)
    wrong = False
    if wordle_tools:
        truth = after.get("remaining")
        wrong = any(a != truth for a in claims["attempts"] if truth is not None)
        known: set[tuple[str, str]] = set()
        for word, marks in after.get("guesses", []):
            known |= set(zip(word, marks, strict=False))
        wrong = wrong or any(pair not in known for pair in claims["pairs"])
    flags["wrong_claim"] = wrong
    leak = bool(re.search(rf"\b{answer}\b", reply, re.IGNORECASE)) and after.get("status") == "in_progress"
    flags["answer_leak"] = leak
    flags["error"] = bool(record["error"])
    return flags


def load_credentials() -> str:
    values = dotenv_values(ENV_FILE)
    key = values.get("DEEPSEEK_API_KEY") or ""
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is not configured")
    return key


def summarize(path: Path) -> None:
    totals: dict[str, Counter] = defaultdict(Counter)
    overall: Counter = Counter()
    for line in path.read_text().splitlines():
        turn = json.loads(line)
        stored_leak = turn["flags"].get("answer_leak", False)
        turn["flags"] = score(turn, turn.get("answer", "XXXXX"))
        turn["flags"]["answer_leak"] = stored_leak or turn["flags"].get("answer_leak", False)
        bucket = totals[turn["kind"]]
        bucket["turns"] += 1
        bucket["fast_path"] += turn["fast_path"]
        overall["turns"] += 1
        for name, failed in turn["flags"].items():
            if failed:
                bucket[name] += 1
                overall[name] += 1
    for kind, bucket in sorted(totals.items()):
        print(f"{kind:14} {dict(bucket)}")
    print("OVERALL", dict(overall))


def tally(turns, answer: str, mode: str, log, totals, examples) -> None:
    for turn in turns:
        flags = score(turn, answer)
        turn["flags"] = flags
        turn["mode"] = mode
        turn["answer"] = answer
        log.write(json.dumps(turn) + "\n")
        log.flush()
        bucket = totals[turn["kind"]]
        bucket["turns"] += 1
        bucket["fast_path"] += turn["fast_path"]
        for name, failed in flags.items():
            if failed:
                bucket[name] += 1
                if len(examples[f"{turn['kind']}:{name}"]) < 2:
                    examples[f"{turn['kind']}:{name}"].append(
                        f"[{mode}/active={turn['active']}] {turn['text']!r} tools={turn['tools']} -> {turn['reply'][:140]!r}"
                    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summarize", help="print failure rates for an existing jsonl log (no model calls)")
    parser.add_argument("--games", type=int, default=0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", default=str(ROOT.parent / "wordle-sim-out"))
    args = parser.parse_args()
    if args.summarize:
        return summarize(Path(args.summarize))
    if games_used() + args.games > HARD_CAP:
        raise SystemExit(f"refusing: {games_used()} used + {args.games} would exceed the {HARD_CAP}-game cap")
    key = load_credentials()
    provider = build_llm({"llm": {"provider": "deepseek_direct", "model": "deepseek-flash"}}, {"DEEPSEEK_API_KEY": key})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    modes = ["normal"] * 6 + ["late"] * 2 + ["inactive"] + ["preopen"] * 2
    totals: dict[str, Counter] = defaultdict(Counter)
    usage = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    log = (out / f"{args.label}.jsonl").open("w")
    for number in range(args.games):
        if games_used() >= HARD_CAP:
            print("hard cap reached; stopping")
            break
        answer = rng.choice(ANSWERS)
        mode = modes[number % len(modes)]
        record_game()
        with tempfile.TemporaryDirectory() as tmp:
            game = Game(provider, answer, mode, random.Random(rng.random()), Path(tmp), key)
            try:
                turns = await game.run()
            except Exception as exc:  # noqa: BLE001
                print(f"game {number} crashed: {type(exc).__name__}: {str(exc)[:150]}")
                continue
        usage.update(game.usage)
        tally(turns, answer, mode, log, totals, examples)
        print(f"game {number + 1}/{args.games} ({mode}, {answer}) used_total={games_used()}", flush=True)
    log.close()
    print("\n== failure counts per kind ==")
    for kind, bucket in sorted(totals.items()):
        failures = {k: v for k, v in bucket.items() if k not in {"turns", "fast_path"}}
        print(f"{kind:14} turns={bucket['turns']:3} fast_path={bucket['fast_path']:3} {failures}")
    print("\n== examples ==")
    for name, items in sorted(examples.items()):
        for item in items:
            print(name, item)
    print(f"\ntokens prompt={usage['prompt']} completion={usage['completion']} games_used_total={games_used()}")


if __name__ == "__main__":
    asyncio.run(main())
