"""Model-facing Wordle function tools, mixed into the Sarjy agent.

Like the weather tool, the model decides when to call them. Each call posts to
``/internal/agent/tool/wordle-*`` (the server owns the game and the answer) and
then tells the web client to open or refresh the popup over ``sarjy.wordle``.
The data message carries no game state: the client refetches the authoritative
board, so the answer can never leak through it.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import httpx
from app.domain.tool_operations import operation_key
from livekit.agents import RunContext, function_tool

SPELLED_RUN = re.compile(r"(?:^|\s)(?:[a-z]\s+){4}[a-z](?:\s|$)")
# Common five-letter words that are ordinary speech, not guesses.
NOT_GUESSES = frozenset(
    [
        "about",
        "popup",
        "close",
        "board",
        "start",
        "begin",
        "check",
        "hello",
        "thank",
        "sorry",
        "hurry",
        "above",
        "after",
        "again",
        "being",
        "could",
        "every",
        "first",
        "great",
        "never",
        "other",
        "right",
        "shall",
        "there",
        "these",
        "thing",
        "think",
        "those",
        "three",
        "today",
        "until",
        "where",
        "which",
        "while",
        "whose",
        "would",
        "wordle",
        "guess",
    ]
)
NOT_SAID = (
    "This was not a guess: the user did not say that word. Ignore this tool, answer what they actually said "
    "normally, and do not mention guesses, marks or counts."
)
CLOSE_REQUEST = re.compile(
    r"^(?:please\s+)?(?:(?:close|hide|dismiss|exit|leave|quit)\s+(?:the\s+)?(?:wordle|game|pop ?up|board|window)"
    r"|(?:go\s+)?back to chat)(?:\s+please)?$"
)
WORDLE_FAILED = "The Wordle tool failed. Tell the user you could not do that right now; do not guess the outcome."


async def publish_wordle(room, action: str) -> None:
    """Tell the web client to open, refresh or close the Wordle popup."""
    await room.local_participant.publish_data(
        json.dumps({"action": action}).encode(), reliable=True, topic="sarjy.wordle"
    )


class WordleTools:
    backend: httpx.AsyncClient
    current_turn: str | None
    publish_wordle: Any
    wordle_spoken: dict[str, list[str]]
    wordle_touched: bool
    wordle_calls: set[str]
    current_text: str
    wordle_active: Any

    async def _wordle_direct(self, path: str, **arguments: str) -> str | None:
        """Run a Wordle tool without the model; returns the exact line to speak (None on failure)."""
        stub = SimpleNamespace(function_call=SimpleNamespace(call_id=f"direct-{path}"))
        await self._wordle_tool(path, stub, **arguments)  # type: ignore[arg-type]
        spoken = self.wordle_spoken.pop(self.current_turn or "", [])
        return " ".join(spoken) or None

    async def _wordle_tool(self, path: str, ctx: RunContext, **arguments: str) -> str:
        if not self.current_turn:
            raise ValueError("TOOL_TURN_REQUIRED")
        word = arguments.get("word")
        if path == "wordle-guess" and word is not None and not said_word(self.current_text, word):
            return NOT_SAID
        call_id = getattr(ctx.function_call, "call_id", "")
        self.wordle_calls.add(self.current_turn)
        try:
            response = await self.backend.post(
                f"/internal/agent/tool/{path}",
                json={
                    **arguments,
                    "turn_id": self.current_turn,
                    "request_id": operation_key(self.current_turn, call_id),
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return WORDLE_FAILED
        if not isinstance(body, dict):
            return WORDLE_FAILED
        spoken = body.get("spoken")
        if isinstance(spoken, str) and spoken:
            self.wordle_spoken.setdefault(self.current_turn, []).append(spoken)
            self.wordle_touched = True
        ui = body.get("ui")
        if self.publish_wordle is not None and ui in {"open", "refresh", "close", "confirm_reset"}:
            try:
                await self.publish_wordle(ui)
            except Exception:
                return f"{body.get('message', '')} (The popup could not be updated; the game state is saved.)"
        return str(body.get("message") or WORDLE_FAILED)

    @function_tool()
    async def play_wordle(self, ctx: RunContext):
        """Start or resume the user's daily Wordle and show the popup. Call it ONLY when the user asks to play Wordle and no game is open (for example "can we play the daily Wordle?"). Never call it while a game is open, and never for a guess."""
        if self.wordle_active():
            return "A Wordle game is already open. Do not start it again; ask for their guess."
        return await self._wordle_tool("wordle-play", ctx)

    @function_tool()
    async def submit_wordle_guess(self, ctx: RunContext, word: str):
        """Submit a Wordle guess that the user embedded in a longer sentence (for example "I think the word might be crane"). Call it only when the user's latest message contains a five-letter word or spelled-out letters meant as a guess, and pass exactly those letters as one word. Never call it for anything else (questions, chat, greetings, hints, closing) and never reuse an earlier word. Marks, guesses left and outcomes come only from this tool's result."""
        return await self._wordle_tool("wordle-guess", ctx, word=word)

    @function_tool()
    async def wordle_status(self, ctx: RunContext):
        """Read the user's Wordle status: guesses so far with letter feedback, and guesses left. Use it whenever the user asks about their board or attempts; never answer those from memory."""
        return await self._wordle_tool("wordle-status", ctx)

    @function_tool()
    async def reset_wordle(self, ctx: RunContext):
        """Use when the user wants to start today's Wordle over in loose phrasing (for example "can we start Wordle over?"). It does NOT reset anything: it asks the user to confirm by voice, and only their spoken "yes" resets the game."""
        return await self._wordle_tool("wordle-reset", ctx)

    @function_tool()
    async def close_wordle(self, ctx: RunContext):
        """Close the Wordle popup when the user wants to stop playing or go back to chat. The game stays saved."""
        return await self._wordle_tool("wordle-close", ctx)


FILLER = frozenset(
    [
        "uh",
        "um",
        "uhm",
        "er",
        "hmm",
        "yeah",
        "yes",
        "okay",
        "ok",
        "so",
        "well",
        "no",
        "please",
        "my",
        "word",
        "is",
        "its",
        "it's",
        "i",
        "think",
        "guess",
        "try",
        "the",
        "a",
        "then",
        "maybe",
        "let's",
        "say",
        "it",
        "just",
    ]
)
STATE_CLAIM = re.compile(
    r"\b(green|yellow|gr[ae]y|correct position|wrong position|not in the word|you (?:won|lost|win|got it)|"
    r"out of (?:guesses|attempts))\b|\b(?:\d|zero|one|two|three|four|five|six)\s+(?:guesses|attempts|tries)\b",
    re.IGNORECASE,
)


def extract_guess(text: str) -> str | None:
    """The word when the WHOLE utterance is a guess: one five-letter word (possibly repeated) or five spelled letters.

    Fillers ("uh", "my word is", "I think it's") are ignored; anything else in the sentence means the model decides.
    """
    raw = re.findall(r"[a-z']+", text.lower().replace("-", " "))
    singles = [t for t in raw if len(t) == 1]
    if len(singles) == 6 and raw[0] == "i":
        singles = singles[1:]  # "I think it's c r a n e": the pronoun is not a letter
    longer = [t for t in raw if len(t) > 1 and t not in FILLER]
    if len(singles) == 5 and not longer:
        return "".join(singles).upper()
    tokens = [t for t in raw if t not in FILLER]
    words = set(tokens)
    if len(words) == 1:
        word = tokens[0]
        if len(word) == 5 and word.isalpha() and word not in NOT_GUESSES:
            return word.upper()
    return None


def is_close_request(text: str) -> bool:
    """A whole-utterance request to close the Wordle popup."""
    return bool(CLOSE_REQUEST.match(re.sub(r"[^a-z' ]", " ", text.lower()).strip()))


def has_guess_candidate(text: str) -> bool:
    """True when the utterance holds a five-letter word or five spelled letters that could be a guess."""
    raw = re.findall(r"[a-z']+", text.lower().replace("-", " "))
    if sum(1 for t in raw if len(t) == 1) >= 5:
        return True
    return any(len(t) == 5 and t.isalpha() and t not in NOT_GUESSES for t in raw)


def said_word(utterance: str, word: str) -> bool:
    """True when the guess is literally in what the user said (as a word or spelled letters)."""
    spoken = re.sub(r"[^a-z]", "", utterance.lower())
    return bool(word) and re.sub(r"[^a-z]", "", word.lower()) in spoken


def claims_game_state(text: str) -> bool:
    """True when prose states marks, attempts or an outcome (only a Wordle tool result may)."""
    return bool(STATE_CLAIM.search(text))
