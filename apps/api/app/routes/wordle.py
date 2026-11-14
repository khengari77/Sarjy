"""Owner-scoped Wordle HTTP adapter.

Responses never contain the answer while the game is in progress.  Logs and
errors carry codes and dates only; the answer is read from storage solely to
build a terminal response.
"""

import time
from collections import deque
from datetime import UTC, date, datetime

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.secrets import secret_matches
from app.domain.wordle import WordleValidationError, resolve_puzzle_date
from app.services.wordle import (
    WordleServiceError,
    WordleSource,
    ensure_game,
    ensure_today,
    game_view,
    player_stats,
    submit_guess,
)

GUESS_LIMIT = 30
GUESS_WINDOW_SECONDS = 60.0


class GuessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guess: str = Field(min_length=1, max_length=20)
    request_id: str = Field(min_length=1, max_length=100)


class GuessRateLimiter:
    """Best-effort process-local guard; the six-attempt cap is the real limit."""

    def __init__(self, limit: int = GUESS_LIMIT, window: float = GUESS_WINDOW_SECONDS):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}

    def allow(self, owner_id: str, now: float | None = None) -> bool:
        instant = time.monotonic() if now is None else now
        hits = self._hits.setdefault(owner_id, deque())
        while hits and hits[0] <= instant - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(instant)
        return True


def _game_payload(view) -> dict:
    payload: dict = {
        "id": view.id,
        "date": view.date,
        "status": view.status,
        "attempts_remaining": view.attempts_remaining,
        "guesses": [{"word": guess.word, "marks": list(guess.marks)} for guess in view.guesses],
    }
    if view.answer is not None:
        payload["answer"] = view.answer
    return payload


async def _game_or_unavailable(db: AsyncSession, *, owner_id: str, day: date):
    try:
        return await ensure_game(db, owner_id=owner_id, day=day)
    except WordleServiceError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc


def _scheduled_day(timezone: str) -> date:
    """Publisher-schedule date, used only when no puzzle row has been ingested.

    Enabled requests never call this: the stored puzzle's upstream date is
    authoritative, so the server clock cannot shift the game to another day.
    """
    try:
        return resolve_puzzle_date(datetime.now(UTC), timezone)
    except WordleValidationError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc


def register_wordle_routes(app, *, config: Settings, database, identity, source: WordleSource):
    limiter = GuessRateLimiter()
    _register_player_routes(app, config=config, database=database, identity=identity, source=source, limiter=limiter)
    _register_refresh_route(app, config=config, database=database, source=source)
    return limiter


def _register_player_routes(app, *, config: Settings, database, identity, source: WordleSource, limiter):
    async def ensure(db: AsyncSession):
        try:
            return await ensure_today(db, source=source, timezone=config.wordle_timezone)
        except WordleServiceError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from exc

    @app.get("/v1/wordle/today")
    async def today(user=Depends(identity), db: AsyncSession = Depends(database)):
        if not config.wordle_enabled:
            day = _scheduled_day(config.wordle_timezone)
            return {"date": day.isoformat(), "available": False, "game": None, "code": "WORDLE_DISABLED"}
        try:
            puzzle = await ensure(db)
        except HTTPException as exc:
            day = _scheduled_day(config.wordle_timezone)
            return {"date": day.isoformat(), "available": False, "game": None, "code": exc.detail["code"]}
        # The ingested row carries the publisher's date; never recompute it.
        game = await _game_or_unavailable(db, owner_id=user.id, day=date.fromisoformat(puzzle.puzzle_date))
        return {
            "date": puzzle.puzzle_date,
            "available": True,
            "game": _game_payload(await game_view(db, game, answer=puzzle.answer)),
        }

    @app.post("/v1/wordle/guesses")
    async def guess(body: GuessInput, user=Depends(identity), db: AsyncSession = Depends(database)):
        if not config.wordle_enabled:
            raise HTTPException(503, detail={"code": "WORDLE_DISABLED"})
        if not limiter.allow(user.id):
            raise HTTPException(429, detail={"code": "WORDLE_RATE_LIMITED"})
        puzzle = await ensure(db)
        try:
            outcome = await submit_guess(
                db,
                owner_id=user.id,
                day=date.fromisoformat(puzzle.puzzle_date),
                answer=puzzle.answer,
                guess=body.guess,
                request_id=body.request_id,
            )
        except WordleServiceError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from exc
        payload = _game_payload(outcome.game)
        payload["marks"] = list(outcome.marks)
        return payload

    @app.get("/v1/wordle/stats")
    async def stats(user=Depends(identity), db: AsyncSession = Depends(database)):
        if not config.wordle_enabled:
            raise HTTPException(503, detail={"code": "WORDLE_DISABLED"})
        result = await player_stats(db, owner_id=user.id)
        return {
            "played": result.played,
            "won": result.won,
            "current_streak": result.current_streak,
            "max_streak": result.max_streak,
        }


def _register_refresh_route(app, *, config: Settings, database, source: WordleSource):
    @app.post("/internal/wordle/refresh")
    async def refresh(request: Request, db: AsyncSession = Depends(database)):
        if not secret_matches(request.headers.get("x-agent-secret", ""), config.agent_service_secret):
            raise HTTPException(401, detail={"code": "WORKER_AUTH_REQUIRED"})
        if not config.wordle_enabled:
            raise HTTPException(503, detail={"code": "WORDLE_DISABLED"})
        try:
            puzzle = await ensure_today(db, source=source, timezone=config.wordle_timezone)
        except WordleServiceError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from exc
        return {"date": puzzle.puzzle_date, "available": True, "source": puzzle.source}
