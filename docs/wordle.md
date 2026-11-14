# Daily Wordle backend

Server-owned daily Wordle: one answer per date, owner-scoped games, and a
narrow API that withholds the answer until the game is terminal.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORDLE_ENABLED` | `false` | Kill switch. Off means no fetch, no game writes and `503` on guess/stats/refresh. |
| `WORDLE_SOURCE_URL` | `https://www.nytimes.com/svc/wordle/v2/{date}.json` | JSON source template; `{date}` is `YYYY-MM-DD`. |
| `WORDLE_TIMEZONE` | `America/New_York` | Publisher's publication schedule used to resolve "today" and the refresh hour. Not the player's local zone. |
| `WORDLE_REFRESH_HOUR` | unset | Optional `0`-`23` local hour for the in-process pre-warm. |

The source must return a JSON object with `solution` (exactly five uppercase
A-Z letters) and `print_date` equal to the requested date. Simulated or
fixture sources are used in tests; CI makes no live network call. Malformed,
missing or mismatched responses produce `WORDLE_UNAVAILABLE`; the service never
falls back to another date or a fabricated answer.

## Fetch-once guarantee

`wordle_puzzles.puzzle_date` is the primary key. `ensure_today()` resolves the
live date from the publisher's schedule zone, with any ingested row acting only
as an upper bound (a future-dated row is never served). The clock is only a
logged fallback when no schedule row exists. `ensure_today()` inserts a
short-lived claim row (`answer` is null) with `INSERT ... ON CONFLICT DO
NOTHING`; only the claimant calls the source. Concurrent requests poll the
stored row with capped exponential backoff, ending the transaction before every
poll on both dialects so the pooled connection is released instead of held
across the wait. Each source attempt is hard-capped by
`asyncio.timeout`, and the lease is asserted at import to exceed the whole
retry budget, so a slow source cannot be reclaimed mid-fetch. An expired
in-flight claim can be reclaimed atomically; a released claim records its
cooldown separately in `retry_after` together with the failure reason in
`last_failure`, so a cooled-down publication delay keeps serving the degraded
fallback instead of flickering to unavailable. The database uniqueness
guarantee, not the scheduler, is authoritative.

A `404` or a response for another date is `NOT_PUBLISHED`: the last ingested
answer is served as an explicitly logged degraded state. A `5xx`, timeout or
network failure is `BROKEN`: the API reports `WORDLE_UNAVAILABLE` instead of
masking the outage with a stale playable puzzle.

Refresh paths:

- `POST /internal/wordle/refresh` with `x-agent-secret` (daily scheduler).
- Lazy `ensure_today()` on the first game request.
- Optional `WORDLE_REFRESH_HOUR` in-process pre-warm.

## API

All player routes require the demo identity cookie. The answer appears only
when `status` is `won` or `lost`; logs contain date/source metadata only.

- `GET /v1/wordle/today` → `{date, available, game}` (`game` is `null` when unavailable or disabled).
- `POST /v1/wordle/guesses` `{guess, request_id}` → marks plus game state; retries with the same
  `request_id` replay without consuming another attempt.
- `GET /v1/wordle/stats` → `played`, `won`, `current_streak`, `max_streak` computed from stored games.

A game allows six guesses. Submission validates five A-Z letters. No bundled
dictionary is shipped because no permissively licensed list is vendored here,
so this is weaker than a dictionary check and is documented rather than hidden;
the source answer validation itself is strict.

Guesses are game state, not conversation turns: they do not create memories or
enter compiled context.

Guesses are serialized per game (`SELECT ... FOR UPDATE` on PostgreSQL, writer
lock on SQLite) and the terminal-status update's affected-row count is checked;
if another request finished the game first, the pending insert is rolled back
and the late guess is rejected with `WORDLE_GAME_OVER`. A duplicate
`request_id` still replays the original outcome, including after the game is
terminal.

## Migration

`migrations/versions/0011_wordle.py` adds `wordle_puzzles`, `wordle_games` and
`wordle_guesses` with owner/date indexes, the unique `(owner_id, puzzle_date)`
game key, the unique `(game_id, attempt)` guess order and the per-owner unique
`(owner_id, request_id)` idempotency key. `wordle_puzzles` carries the claim
lease plus a separate `retry_after` cooldown. Downgrade drops only the Wordle
tables. PostgreSQL deployments also get RLS and revoked `anon`/`authenticated`
grants on the new tables.
