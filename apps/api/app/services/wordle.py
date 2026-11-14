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
# Total wall-clock bound for ONE attempt, independent of httpx's per-operation
# timeout (connect/read/write/pool can each consume their own budget).
FETCH_TIMEOUT_SECONDS = 8.0
BACKOFF_SECONDS = (0.1, 0.25)
RETRY_BUDGET_SECONDS = MAX_FETCH_ATTEMPTS * FETCH_TIMEOUT_SECONDS + sum(BACKOFF_SECONDS)
# The lease must outlive the whole bounded retry budget with margin.  If a
# lease could expire mid-fetch, another request reclaims the row and the source
# is called twice for one puzzle.
CLAIM_LEASE_SECONDS = RETRY_BUDGET_SECONDS * 2 + 10


def validate_claim_lease(lease: timedelta, retry_budget: timedelta) -> None:
    """Fail fast if a claim lease can expire before its retry budget closes."""
    if lease <= retry_budget:
        raise ValueError("WORDLE_CLAIM_LEASE_TOO_SHORT")


CLAIM_LEASE = timedelta(seconds=CLAIM_LEASE_SECONDS)
validate_claim_lease(CLAIM_LEASE, timedelta(seconds=RETRY_BUDGET_SECONDS))
RETRY_COOLDOWN = timedelta(seconds=60)
# A loser must wait out the full lease: returning 503 while the winner is still
# inside its bounded fetch makes every concurrent tab fail during network lag.
CLAIM_WAIT_SECONDS = CLAIM_LEASE_SECONDS + 5.0
CLAIM_POLL_SECONDS = 0.05
CLAIM_POLL_MAX_SECONDS = 1.0
SUBMIT_RETRIES = 4
# One failure vocabulary shared by the source boundary and the service.  The
# persisted reason lets a cooled-down row keep reporting why it is cooling.
WORDLE_NOT_PUBLISHED = "WORDLE_NOT_PUBLISHED"
WORDLE_SOURCE_FAILED = "WORDLE_SOURCE_FAILED"
WORDLE_UNAVAILABLE = "WORDLE_UNAVAILABLE"


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
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, transport=self._transport) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise WordleSourceError(WORDLE_NOT_PUBLISHED) from exc
            raise WordleSourceError(WORDLE_SOURCE_FAILED) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WordleSourceError(WORDLE_SOURCE_FAILED) from exc
        # Only a 404 or a payload naming a valid, different date is a
        # publication delay.  A malformed body is a broken source, never a
        # reason to serve yesterday's puzzle indefinitely.
        if not isinstance(payload, dict):
            raise WordleSourceError(WORDLE_SOURCE_FAILED)
        returned_date = payload.get("print_date", payload.get("date"))
        if not isinstance(returned_date, str) or not returned_date:
            raise WordleSourceError(WORDLE_SOURCE_FAILED)
        if returned_date != puzzle_date.isoformat():
            try:
                date.fromisoformat(returned_date)
            except ValueError as exc:
                raise WordleSourceError(WORDLE_SOURCE_FAILED) from exc
            raise WordleSourceError(WORDLE_NOT_PUBLISHED)
        return parse_source_payload(payload, puzzle_date, source=self.label)


def _iso(instant: datetime) -> str:
    """Fixed-width UTC strings so lease/cooldown comparisons are monotonic."""
    if instant.tzinfo is None:
        raise ValueError("WORDLE_TIMESTAMP_NAIVE")
    return instant.astimezone(UTC).isoformat(timespec="microseconds")


async def _load_puzzle(db: AsyncSession, key: str) -> WordlePuzzle | None:
    return await db.scalar(
        select(WordlePuzzle).where(WordlePuzzle.puzzle_date == key).execution_options(populate_existing=True)
    )


async def _latest_ingested_puzzle(db: AsyncSession, *, on_or_before: date | None = None) -> WordlePuzzle | None:
    """Newest schedule row carrying an answer, optionally date-bounded in SQL."""
    statement = select(WordlePuzzle).where(WordlePuzzle.answer.is_not(None))
    if on_or_before is not None:
        statement = statement.where(WordlePuzzle.puzzle_date <= on_or_before.isoformat())
    return await db.scalar(
        statement.order_by(WordlePuzzle.puzzle_date.desc()).limit(1).execution_options(populate_existing=True)
    )


def _servable_date(latest_date: date | None, wall_date: date) -> date:
    """The schedule is an upper bound: never serve a date after the clock."""
    if latest_date is None or latest_date > wall_date:
        return wall_date
    return latest_date


async def scheduled_puzzle_date(db: AsyncSession, instant: datetime, timezone: str) -> date:
    """The date to fetch/advance to, bounded by the publisher-schedule clock.

    The clock decides when to attempt the next publication; an ingested row can
    never pull the live date forward.  Any served *fallback* is clamped with
    ``_servable_date`` so an early publication, manual refresh, DST shift or
    clock skew cannot expose a future answer.
    """
    return resolve_puzzle_date(instant, timezone)


async def _servable_ingested_puzzle(db: AsyncSession, wall_date: date) -> WordlePuzzle | None:
    """Newest stored answer that is not in the future relative to the schedule."""
    latest = await _latest_ingested_puzzle(db, on_or_before=wall_date)
    if latest is None:
        return None
    try:
        latest_date = date.fromisoformat(latest.puzzle_date)
    except ValueError:
        logger.warning("wordle schedule row is corrupt date=%s; ignoring", latest.puzzle_date)
        return None
    if _servable_date(latest_date, wall_date) != latest_date:
        logger.warning("wordle future-dated row ignored date=%s", latest.puzzle_date)
        return None
    return latest


async def ensure_today(
    db: AsyncSession,
    *,
    source: WordleSource,
    timezone: str,
    now: datetime | None = None,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> WordlePuzzle:
    """Return the live stored puzzle, keyed on the source publication schedule.

    A not-yet-published next date serves the last ingested answer as an
    explicitly logged degraded state.  A broken source (5xx/network/timeout)
    raises ``WORDLE_UNAVAILABLE``; a stale answer must never mask an outage.
    """
    instant = now or datetime.now(UTC)
    try:
        day = await scheduled_puzzle_date(db, instant, timezone)
    except WordleValidationError as exc:
        raise WordleServiceError(exc.code, 503) from exc
    try:
        return await ensure_puzzle(db, day, source=source, now=instant, sleep=sleep)
    except WordleServiceError as exc:
        if exc.code != WORDLE_NOT_PUBLISHED:
            raise
        fallback = await _servable_ingested_puzzle(db, day)
        if fallback is None:
            raise WordleServiceError(WORDLE_UNAVAILABLE, 503) from exc
        logger.warning(
            "wordle date not published; serving stored date=%s degraded (wanted date=%s)",
            fallback.puzzle_date,
            day.isoformat(),
        )
        return fallback


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
            "retry_after": None,
            "last_failure": None,
            "last_attempt_at": now_iso,
        },
        index_elements=["puzzle_date"],
    )
    await db.commit()

    if inserted.rowcount != 1:
        contended = await _contend_for_claim(
            db, key, claim_id=claim_id, now_iso=now_iso, claim_expires_at=claim_expires_at, sleep=sleep
        )
        if contended is not None:
            return contended

    fetched, failure = await _fetch_with_retries(source, day, sleep)
    if fetched is None:
        await _release_claim(db, key, claim_id=claim_id, instant=instant, now_iso=now_iso, failure=failure)
        if failure == WORDLE_NOT_PUBLISHED:
            raise WordleServiceError(WORDLE_NOT_PUBLISHED, 503)
        raise WordleServiceError(WORDLE_UNAVAILABLE, 503)

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
            retry_after=None,
            last_failure=None,
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
    raise WordleServiceError(WORDLE_UNAVAILABLE, 503)


async def _contend_for_claim(
    db: AsyncSession,
    key: str,
    *,
    claim_id: str,
    now_iso: str,
    claim_expires_at: str,
    sleep: Callable[[float], Awaitable[object]],
) -> WordlePuzzle | None:
    """Resolve an existing row after losing the insert race.

    Returns a stored answer to serve, or ``None`` when this caller now owns the
    claim and should fetch.  Only an in-flight lease that lapsed may be stolen;
    a released row is governed by the separate ``retry_after`` cooldown, so it
    is never reclaimed while its retry budget is still open.
    """
    reclaimed = await db.execute(
        update(WordlePuzzle)
        .where(
            WordlePuzzle.puzzle_date == key,
            WordlePuzzle.answer.is_(None),
            WordlePuzzle.claim_id.is_not(None),
            WordlePuzzle.claim_expires_at <= now_iso,
        )
        .values(
            claim_id=claim_id,
            claim_expires_at=claim_expires_at,
            retry_after=None,
            last_failure=None,
            last_attempt_at=now_iso,
        )
    )
    await db.commit()
    if reclaimed.rowcount == 1:
        return None

    current = await _load_puzzle(db, key)
    if current is not None and current.answer:
        return current
    if current is not None and current.claim_id is None:
        if current.retry_after is not None and current.retry_after > now_iso:
            # Preserve why the row is cooling down: a publication delay must
            # keep serving the degraded fallback, not turn into an outage.
            if current.last_failure == WORDLE_NOT_PUBLISHED:
                raise WordleServiceError(WORDLE_NOT_PUBLISHED, 503)
            raise WordleServiceError(WORDLE_UNAVAILABLE, 503)
        claimed = await db.execute(
            update(WordlePuzzle)
            .where(
                WordlePuzzle.puzzle_date == key,
                WordlePuzzle.answer.is_(None),
                WordlePuzzle.claim_id.is_(None),
                or_(
                    WordlePuzzle.retry_after.is_(None),
                    WordlePuzzle.retry_after <= now_iso,
                ),
            )
            .values(
                claim_id=claim_id,
                claim_expires_at=claim_expires_at,
                retry_after=None,
                last_failure=None,
                last_attempt_at=now_iso,
            )
        )
        await db.commit()
        if claimed.rowcount == 1:
            return None

    stored = await _wait_for_answer(db, key, sleep)
    if stored is not None:
        return stored
    raise WordleServiceError(WORDLE_UNAVAILABLE, 503)


async def _fetch_with_retries(
    source: WordleSource,
    day: date,
    sleep: Callable[[float], Awaitable[object]],
) -> tuple[FetchedPuzzle | None, str | None]:
    """Fetch once per attempt; report why it ultimately failed.

    "Not published" is terminal and never retried: it is a normal schedule
    delay.  Every other failure is retried within the bounded retry budget and
    reported as broken.
    """
    failure = WORDLE_SOURCE_FAILED
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
                return await source.fetch(day), None
        except TimeoutError:
            failure = WORDLE_SOURCE_FAILED
            logger.warning(
                "wordle fetch timed out date=%s source=%s attempt=%s budget=%ss",
                day.isoformat(),
                source.label,
                attempt,
                FETCH_TIMEOUT_SECONDS,
            )
        except WordleSourceError as exc:
            logger.warning(
                "wordle fetch failed date=%s source=%s attempt=%s code=%s",
                day.isoformat(),
                source.label,
                attempt,
                exc.code,
            )
            if exc.code == WORDLE_NOT_PUBLISHED:
                return None, WORDLE_NOT_PUBLISHED
            failure = WORDLE_SOURCE_FAILED
        # Injected test doubles can fail in other ways; treat any boundary
        # failure as unavailable instead of propagating an internal 500.
        except Exception as exc:  # noqa: BLE001 - external boundary
            failure = WORDLE_SOURCE_FAILED
            logger.warning(
                "wordle fetch failed date=%s source=%s attempt=%s error=%s",
                day.isoformat(),
                source.label,
                attempt,
                type(exc).__name__,
            )
        if attempt < MAX_FETCH_ATTEMPTS:
            await sleep(BACKOFF_SECONDS[attempt - 1])
    return None, failure


async def _release_claim(
    db: AsyncSession,
    key: str,
    *,
    claim_id: str,
    instant: datetime,
    now_iso: str,
    failure: str | None,
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
            claim_expires_at=None,
            retry_after=_iso(instant + RETRY_COOLDOWN),
            last_failure=failure,
            last_attempt_at=now_iso,
        )
    )
    await db.commit()


def poll_delays(
    deadline: float,
    *,
    initial: float = CLAIM_POLL_SECONDS,
    maximum: float = CLAIM_POLL_MAX_SECONDS,
):
    """Exponential backoff between stored-answer polls, capped at ``maximum``."""
    elapsed = 0.0
    delay = initial
    while elapsed < deadline:
        yield delay
        elapsed += delay
        delay = min(delay * 2, maximum)


async def _wait_for_answer(
    db: AsyncSession, key: str, sleep: Callable[[float], Awaitable[object]]
) -> WordlePuzzle | None:
    # Read, then end the transaction before sleeping.  On SQLite that makes the
    # winner's commit visible past the read snapshot; on PostgreSQL it returns
    # the pooled connection instead of pinning it across the whole backoff.
    for delay in poll_delays(CLAIM_WAIT_SECONDS):
        row = await _load_puzzle(db, key)
        if row is not None and row.answer:
            return row
        await db.commit()
        await sleep(delay)
    await db.commit()
    return None


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
        raise WordleServiceError(WORDLE_UNAVAILABLE, 503)
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


async def _replay_guess(
    db: AsyncSession,
    *,
    game: WordleGame,
    owner_id: str,
    request_id: str,
    word: str,
    answer: str,
) -> GuessOutcome | None:
    """Return the stored outcome for a duplicate request id, if any."""
    existing = await db.scalar(
        select(WordleGuess)
        .where(WordleGuess.owner_id == owner_id, WordleGuess.request_id == request_id)
        .execution_options(populate_existing=True)
    )
    if existing is None:
        return None
    if existing.game_id != game.id or existing.word != word:
        await db.rollback()
        raise WordleServiceError("WORDLE_REQUEST_CONFLICT", 409)
    return GuessOutcome(
        game=await game_view(db, game, answer=answer),
        word=existing.word,
        marks=tuple(existing.marks),
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
    _race_hook: Callable[[], Awaitable[object]] | None = None,
) -> GuessOutcome:
    """Score one guess atomically, replaying a duplicate ``request_id``.

    ``_race_hook`` is a deterministic test seam fired after the status read and
    before the insert; production callers never pass it.
    """
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
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if game is None:
            game = await ensure_game(db, owner_id=owner_id, day=day, now=instant)
        replay = await _replay_guess(db, game=game, owner_id=owner_id, request_id=request_id, word=word, answer=answer)
        if replay is not None:
            await db.rollback()
            return replay
        if game.status != WordleStatus.IN_PROGRESS.value:
            await db.rollback()
            raise WordleServiceError("WORDLE_GAME_OVER", 409)
        if _race_hook is not None:
            await _race_hook()

        rows = (await db.scalars(select(WordleGuess).where(WordleGuess.game_id == game.id))).all()
        attempt = len(rows) + 1
        if attempt > MAX_ATTEMPTS:
            await db.rollback()
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
                    "owner_id": owner_id,
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
        closed = await db.execute(
            update(WordleGame)
            .where(
                WordleGame.id == game.id,
                WordleGame.status == WordleStatus.IN_PROGRESS.value,
            )
            .values(status=status, updated_at=now_iso)
        )
        if closed.rowcount != 1:
            # Someone else finished the game between our status read and this
            # write.  Roll back so the unsanctioned guess insert does not
            # survive on a won/lost game.
            await db.rollback()
            raise WordleServiceError("WORDLE_GAME_OVER", 409)
        await db.commit()
        fresh = await db.scalar(
            select(WordleGame).where(WordleGame.id == game.id).execution_options(populate_existing=True)
        )
        if fresh is None:
            raise WordleServiceError(WORDLE_UNAVAILABLE, 503)
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
