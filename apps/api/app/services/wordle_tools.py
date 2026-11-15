"""Model-facing Wordle tools: one reusable effect per tool call.

Every function returns a plain dict the model reads to word its spoken reply:
``message`` says exactly what happened and ``ui`` tells the voice worker how to
sync the web popup (it never carries the answer; the client refetches the
authoritative game). The answer appears in a result only once the server has
released it for a finished game.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.wordle import (
    WordleServiceError,
    WordleSource,
    ensure_game,
    ensure_today,
    game_view,
    submit_guess,
)

DISABLED = {
    "status": "disabled",
    "message": "Wordle is switched off in this deployment, so I cannot start or play it right now.",
}
REVEAL_HINT = "State only these facts, never marks or outcomes from memory. If the user wants the answer, they should say 'reveal the answer' and confirm; you must not state it."
ERROR_MESSAGES = {
    "WORDLE_GUESS_INVALID": "That is not a valid five-letter word guess. Ask the user for a five-letter word.",
    "WORDLE_GAME_OVER": "Today's game is already finished, so no more guesses are possible.",
    "WORDLE_GUESS_CONFLICT": "The guess could not be recorded; ask the user to try again.",
    "WORDLE_REQUEST_CONFLICT": "The guess could not be recorded; ask the user to try again.",
}
UNAVAILABLE = "Today's Wordle puzzle is not available right now. Tell the user it is temporarily unavailable."


def _marks_text(word: str, marks: tuple[str, ...]) -> str:
    return f"{word}: " + ", ".join(f"{letter} {mark}" for letter, mark in zip(word, marks, strict=True))


def describe_game(view) -> str:
    lines = [_marks_text(guess.word, guess.marks) for guess in view.guesses]
    board = "No guesses yet." if not lines else "Guesses so far: " + "; ".join(lines) + "."
    if view.status == "won":
        return f"The user won today's Wordle. {board}"
    if view.status != "in_progress":
        return f"The user lost today's Wordle; the answer was {view.answer}. {board}"
    return f"The game is in progress with {view.attempts_remaining} guesses left. {board} {REVEAL_HINT}"


async def _today(db: AsyncSession, owner_id: str, source: WordleSource, timezone: str):
    puzzle = await ensure_today(db, source=source, timezone=timezone)
    day = date.fromisoformat(puzzle.puzzle_date)
    game = await ensure_game(db, owner_id=owner_id, day=day)
    return puzzle, day, await game_view(db, game, answer=puzzle.answer)


def _error(code: str) -> dict:
    return {"status": "error", "code": code, "message": ERROR_MESSAGES.get(code, UNAVAILABLE)}


async def wordle_tool(
    db: AsyncSession,
    *,
    action: str,
    owner_id: str,
    enabled: bool,
    source: WordleSource,
    timezone: str,
    word: str | None = None,
    request_id: str = "",
) -> dict:
    """Run ``play``, ``guess``, ``status`` or ``close`` for the conversation owner."""
    if not enabled:
        return dict(DISABLED)
    if action == "close":
        return {"status": "closed", "ui": "close", "message": "The Wordle popup was closed. The game is saved."}
    try:
        puzzle, day, view = await _today(db, owner_id, source, timezone)
        if action == "guess":
            if view.status != "in_progress":
                return {**_error("WORDLE_GAME_OVER"), "ui": "open", "message": describe_game(view)}
            tried = (word or "").strip().upper()
            if any(guess.word == tried for guess in view.guesses):
                # An exact repeat (voice echo or a manual entry) never spends an attempt.
                return {
                    "status": "repeat",
                    "ui": "refresh",
                    "message": f"The user already tried {tried}; no attempt was used. " + describe_game(view),
                }
            outcome = await submit_guess(
                db,
                owner_id=owner_id,
                day=day,
                answer=puzzle.answer,
                guess=word or "",
                request_id=f"tool-{request_id}",
            )
            view = outcome.game
    except WordleServiceError as exc:
        return _error(exc.code)
    prefix = ""
    if action == "play":
        prefix = (
            "Started or resumed today's Wordle and showed it. From now on every five-letter word the user says is a "
            "guess for submit_wordle_guess; do not call play_wordle again. "
        )
    return {
        "status": view.status,
        "ui": "open" if action != "status" else "refresh",
        "message": prefix + describe_game(view),
    }
