"""Daily Wordle source adapter, fetch-once storage and game services.

The database primary key on ``wordle_puzzles.puzzle_date`` is the fetch-once
guarantee.  A request first inserts a short-lived claim row; only the caller
that inserted (or atomically reclaimed an expired claim) may call the source.
Everyone else waits briefly for the stored answer instead of calling out.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.wordle import (
    MAX_ATTEMPTS,
    WordleStatus,
    WordleValidationError,
    normalize_guess,
    resolve_puzzle_date,
    score_guess,
    status_after_guess,
    validate_answer,
)
from app.infrastructure.database import WordleGame, WordleGuess, WordlePuzzle
from app.infrastructure.upsert import insert_ignore

logger = logging.getLogger(__name__)

MAX_FETCH_ATTEMPTS = 3
BACKOFF_SECONDS = (0.1, 0.25)
CLAIM_LEASE = timedelta(seconds=30)
RETRY_COOLDOWN = timedelta(seconds=60)
CLAIM_WAIT_SECONDS = 2.0
CLAIM_POLL_SECONDS = 0.05
SUBMIT_RETRIES = 4


class WordleSourceError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class WordleServiceError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class FetchedPuzzle:
    answer: str
    source: str
    payload_hash: str


class WordleSource(Protocol):
    label: str

    async def fetch(self, puzzle_date: date) -> FetchedPuzzle: ...


@dataclass(frozen=True)
class GuessView:
    word: str
    marks: tuple[str, ...]


@dataclass(frozen=True)
class GameView:
    id: str
    date: str
    status: str
    guesses: tuple[GuessView, ...]
    answer: str | None
    attempts_remaining: int


@dataclass(frozen=True)
class GuessOutcome:
    game: GameView
    word: str
    marks: tuple[str, ...]


@dataclass(frozen=True)
class WordleStats:
    played: int
    won: int
    current_streak: int
    max_streak: int


def parse_source_payload(payload: object, puzzle_date: date, *, source: str) -> FetchedPuzzle:
    """Validate one source response; never accept another day's answer."""
    if not isinstance(payload, dict):
        raise WordleSourceError("WORDLE_SOURCE_INVALID")
    try:
        answer = validate_answer(payload.get("solution"))
    except WordleValidationError as exc:
        raise WordleSourceError("WORDLE_SOURCE_INVALID") from exc
    returned_date = payload.get("print_date", payload.get("date"))
    if returned_date != puzzle_date.isoformat():
        raise WordleSourceError("WORDLE_SOURCE_DATE_MISMATCH")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return FetchedPuzzle(
        answer=answer,
        source=source,
        payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


class HttpWordleSource:
    """Configurable JSON source.  The only external call in the Wordle slice."""

    def __init__(self, url_template: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.url_template = url_template
        self.label = url_template[:200]
        self._transport = transport

    async def fetch(self, puzzle_date: date) -> FetchedPuzzle:
        url = self.url_template.format(date=puzzle_date.isoformat())
        try:
            async with httpx.AsyncClient(timeout=8, transport=self._transport) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WordleSourceError("WORDLE_SOURCE_FAILED") from exc
        return parse_source_payload(payload, puzzle_date, source=self.label)


def _iso(instant: datetime) -> str:
    return instant.isoformat()


async def _load_puzzle(db: AsyncSession, key: str) -> WordlePuzzle | None:
    return await db.scalar(
        select(WordlePuzzle).where(WordlePuzzle.puzzle_date == key).execution_options(populate_existing=True)
    )


async def ensure_today(
    db: AsyncSession,
    *,
    source: WordleSource,
    timezone: str,
    now: datetime | None = None,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> WordlePuzzle:
    """Return today's stored puzzle, fetching it at most once per date."""
    instant = now or datetime.now(UTC)
    try:
        day = resolve_puzzle_date(instant, timezone)
    except WordleValidationError as exc:
        raise WordleServiceError(exc.code, 503) from exc
    return await ensure_puzzle(db, day, source=source, now=instant, sleep=sleep)


async def ensure_puzzle(
    db: AsyncSession,
    day: date,
    *,
    source: WordleSource,
    now: datetime | None = None,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> WordlePuzzle:
    instant = now or datetime.now(UTC)
    key = day.isoformat()
    existing = await _load_puzzle(db, key)
    if existing is not None and existing.answer:
        return existing

    claim_id = str(uuid4())
    now_iso = _iso(instant)
    claim_expires_at = _iso(instant + CLAIM_LEASE)
    inserted = await insert_ignore(
        db,
        WordlePuzzle,
        {
            "puzzle_date": key,
            "answer": None,
            "source": source.label,
            "fetched_at": None,
            "payload_hash": None,
            "claim_id": claim_id,
            "claim_expires_at": claim_expires_at,
            "last_attempt_at": now_iso,
        },
        index_elements=["puzzle_date"],
    )
    await db.commit()

    if inserted.rowcount != 1:
        reclaimed = await db.execute(
            update(WordlePuzzle)
            .where(
                WordlePuzzle.puzzle_date == key,
                WordlePuzzle.answer.is_(None),
                or_(
                    WordlePuzzle.claim_expires_at.is_(None),
                    WordlePuzzle.claim_expires_at <= now_iso,
                ),
            )
            .values(claim_id=claim_id, claim_expires_at=claim_expires_at, last_attempt_at=now_iso)
        )
        await db.commit()
        if reclaimed.rowcount != 1:
            # A released claim stores a retry cooldown in ``claim_expires_at``
            # with no claimant.  There is no in-flight fetch to wait for, so
            # fail fast instead of polling.
            current = await _load_puzzle(db, key)
            if current is not None and current.answer is None and current.claim_id is None:
                raise WordleServiceError("WORDLE_UNAVAILABLE", 503)
            stored = await _wait_for_answer(db, key, sleep)
            if stored is not None:
                return stored
            raise WordleServiceError("WORDLE_UNAVAILABLE", 503)

    fetched = await _fetch_with_retries(source, day, sleep)
    if fetched is None:
        await _release_claim(db, key, claim_id=claim_id, instant=instant, now_iso=now_iso)
        raise WordleServiceError("WORDLE_UNAVAILABLE", 503)

    completed = await db.execute(
        update(WordlePuzzle)
        .where(WordlePuzzle.puzzle_date == key, WordlePuzzle.claim_id == claim_id)
        .values(
            answer=fetched.answer,
            source=fetched.source,
            fetched_at=now_iso,
            payload_hash=fetched.payload_hash,
            claim_id=None,
            claim_expires_at=None,
            last_attempt_at=now_iso,
        )
    )
    await db.commit()
    stored = await _load_puzzle(db, key)
    if completed.rowcount == 1 and stored is not None and stored.answer:
        logger.info("wordle fetch stored date=%s source=%s", key, fetched.source)
        return stored
    if stored is not None and stored.answer:
        return stored
    raise WordleServiceError("WORDLE_UNAVAILABLE", 503)


async def _fetch_with_retries(
    source: WordleSource,
    day: date,
    sleep: Callable[[float], Awaitable[object]],
) -> FetchedPuzzle | None:
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            return await source.fetch(day)
        except WordleSourceError as exc:
            logger.warning(
                "wordle fetch failed date=%s source=%s attempt=%s code=%s",
                day.isoformat(),
                source.label,
                attempt,
                exc.code,
            )
        # Injected test doubles can fail in other ways; treat any boundary
        # failure as unavailable instead of propagating an internal 500.
        except Exception as exc:  # noqa: BLE001 - external boundary
            logger.warning(
                "wordle fetch failed date=%s source=%s attempt=%s error=%s",
                day.isoformat(),
                source.label,
                attempt,
                type(exc).__name__,
            )
        if attempt < MAX_FETCH_ATTEMPTS:
            await sleep(BACKOFF_SECONDS[attempt - 1])
    return None


async def _release_claim(
    db: AsyncSession,
    key: str,
    *,
    claim_id: str,
    instant: datetime,
    now_iso: str,
) -> None:
    await db.execute(
        update(WordlePuzzle)
        .where(
            WordlePuzzle.puzzle_date == key,
            WordlePuzzle.claim_id == claim_id,
            WordlePuzzle.answer.is_(None),
        )
        .values(
            claim_id=None,
            claim_expires_at=_iso(instant + RETRY_COOLDOWN),
            last_attempt_at=now_iso,
        )
    )
    await db.commit()


async def _wait_for_answer(
    db: AsyncSession, key: str, sleep: Callable[[float], Awaitable[object]]
) -> WordlePuzzle | None:
    iterations = max(1, int(CLAIM_WAIT_SECONDS / CLAIM_POLL_SECONDS))
    row: WordlePuzzle | None = None
    for _ in range(iterations):
        # End the read transaction before each poll so a commit made by the
        # fetching process becomes visible (SQLite holds a read snapshot for
        # the life of an open transaction; PostgreSQL uses read committed).
        await db.commit()
        row = await _load_puzzle(db, key)
        if row is not None and row.answer:
            return row
        await sleep(CLAIM_POLL_SECONDS)
    await db.commit()
    row = await _load_puzzle(db, key)
    return row if row is not None and row.answer else None


async def ensure_game(
    db: AsyncSession,
    *,
    owner_id: str,
    day: date,
    now: datetime | None = None,
) -> WordleGame:
    instant = now or datetime.now(UTC)
    now_iso = _iso(instant)
    key = day.isoformat()
    await insert_ignore(
        db,
        WordleGame,
        {
            "id": str(uuid4()),
            "owner_id": owner_id,
            "puzzle_date": key,
            "status": WordleStatus.IN_PROGRESS.value,
            "created_at": now_iso,
            "updated_at": now_iso,
        },
        index_elements=["owner_id", "puzzle_date"],
    )
    await db.commit()
    game = await db.scalar(
        select(WordleGame)
        .where(WordleGame.owner_id == owner_id, WordleGame.puzzle_date == key)
        .execution_options(populate_existing=True)
    )
    if game is None:
        raise WordleServiceError("WORDLE_UNAVAILABLE", 503)
    return game


async def game_view(db: AsyncSession, game: WordleGame, *, answer: str) -> GameView:
    rows = (
        await db.scalars(select(WordleGuess).where(WordleGuess.game_id == game.id).order_by(WordleGuess.attempt))
    ).all()
    terminal = game.status != WordleStatus.IN_PROGRESS.value
    return GameView(
        id=game.id,
        date=game.puzzle_date,
        status=game.status,
        guesses=tuple(GuessView(word=row.word, marks=tuple(row.marks)) for row in rows),
        answer=answer if terminal else None,
        attempts_remaining=max(0, MAX_ATTEMPTS - len(rows)),
    )


async def submit_guess(
    db: AsyncSession,
    *,
    owner_id: str,
    day: date,
    answer: str,
    guess: str,
    request_id: str,
    now: datetime | None = None,
) -> GuessOutcome:
    """Score one guess atomically, replaying a duplicate ``request_id``."""
    try:
        word = normalize_guess(guess)
    except WordleValidationError as exc:
        raise WordleServiceError(exc.code, 422) from exc
    instant = now or datetime.now(UTC)
    now_iso = _iso(instant)
    key = day.isoformat()

    for _ in range(SUBMIT_RETRIES):
        game = await db.scalar(
            select(WordleGame)
            .where(WordleGame.owner_id == owner_id, WordleGame.puzzle_date == key)
            .execution_options(populate_existing=True)
        )
        if game is None:
            game = await ensure_game(db, owner_id=owner_id, day=day, now=instant)
        existing = await db.scalar(
            select(WordleGuess).where(WordleGuess.request_id == request_id).execution_options(populate_existing=True)
        )
        if existing is not None:
            if existing.game_id != game.id or existing.word != word:
                raise WordleServiceError("WORDLE_REQUEST_CONFLICT", 409)
            return GuessOutcome(
                game=await game_view(db, game, answer=answer),
                word=existing.word,
                marks=tuple(existing.marks),
            )
        if game.status != WordleStatus.IN_PROGRESS.value:
            raise WordleServiceError("WORDLE_GAME_OVER", 409)

        rows = (await db.scalars(select(WordleGuess).where(WordleGuess.game_id == game.id))).all()
        attempt = len(rows) + 1
        if attempt > MAX_ATTEMPTS:
            raise WordleServiceError("WORDLE_GAME_OVER", 409)
        marks = tuple(mark.value for mark in score_guess(answer, word))
        status = status_after_guess(answer, word, attempt).value

        try:
            inserted = await insert_ignore(
                db,
                WordleGuess,
                {
                    "id": str(uuid4()),
                    "game_id": game.id,
                    "attempt": attempt,
                    "word": word,
                    "marks": list(marks),
                    "request_id": request_id,
                    "created_at": now_iso,
                },
                index_elements=["game_id", "attempt"],
            )
        except IntegrityError:
            await db.rollback()
            continue
        if inserted.rowcount != 1:
            # ON CONFLICT DO NOTHING leaves the transaction open with a stale
            # read snapshot; roll back before re-reading the winning attempt.
            await db.rollback()
            continue

        # Insert the guess and close the game in one transaction so a crash
        # cannot leave a terminal guess attached to an in-progress game.
        await db.execute(
            update(WordleGame)
            .where(
                WordleGame.id == game.id,
                WordleGame.status == WordleStatus.IN_PROGRESS.value,
            )
            .values(status=status, updated_at=now_iso)
        )
        await db.commit()
        fresh = await db.get(WordleGame, game.id)
        if fresh is None:
            raise WordleServiceError("WORDLE_UNAVAILABLE", 503)
        return GuessOutcome(
            game=await game_view(db, fresh, answer=answer),
            word=word,
            marks=marks,
        )
    raise WordleServiceError("WORDLE_GUESS_CONFLICT", 409)


def _next_day(previous: date, current: str) -> bool:
    try:
        played = date.fromisoformat(current)
    except ValueError:
        return False
    return played == previous + timedelta(days=1)


async def player_stats(db: AsyncSession, *, owner_id: str) -> WordleStats:
    rows = (
        await db.scalars(
            select(WordleGame)
            .where(
                WordleGame.owner_id == owner_id,
                WordleGame.status.in_((WordleStatus.WON.value, WordleStatus.LOST.value)),
            )
            .order_by(WordleGame.puzzle_date)
        )
    ).all()
    won = sum(1 for row in rows if row.status == WordleStatus.WON.value)
    current = 0
    best = 0
    previous: date | None = None
    for row in rows:
        if row.status == WordleStatus.WON.value:
            current = current + 1 if previous is not None and _next_day(previous, row.puzzle_date) else 1
            best = max(best, current)
        else:
            current = 0
        try:
            previous = date.fromisoformat(row.puzzle_date)
        except ValueError:
            previous = None
    return WordleStats(played=len(rows), won=won, current_streak=current, max_streak=best)


def seconds_until_hour(timezone: str, hour: int, now: datetime | None = None) -> float:
    instant = now or datetime.now(UTC)
    local = instant.astimezone(ZoneInfo(timezone))
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return (target - local).total_seconds()


async def wordle_refresh_loop(sessions, source: WordleSource, *, timezone: str, hour: int) -> None:
    """Optional in-process daily pre-warm; the DB remains the real guarantee."""
    while True:
        try:
            await asyncio.sleep(seconds_until_hour(timezone, hour))
        except WordleValidationError as exc:
            logger.warning("wordle scheduled refresh disabled code=%s", exc.code)
            return
        async with sessions() as db:
            try:
                await ensure_today(db, source=source, timezone=timezone)
            except (WordleServiceError, WordleValidationError) as exc:
                code = getattr(exc, "code", type(exc).__name__)
                logger.warning("wordle scheduled refresh failed code=%s", code)


__all__ = [
    "FetchedPuzzle",
    "GameView",
    "GuessOutcome",
    "GuessView",
    "HttpWordleSource",
    "WordleServiceError",
    "WordleSource",
    "WordleSourceError",
    "WordleStats",
    "ensure_game",
    "ensure_puzzle",
    "ensure_today",
    "game_view",
    "parse_source_payload",
    "player_stats",
    "seconds_until_hour",
    "submit_guess",
    "wordle_refresh_loop",
]
