# Daily Wordle backend

Server-owned daily Wordle: one answer per date, owner-scoped games, and a
narrow API that withholds the answer until the game is terminal.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORDLE_ENABLED` | `false` | Kill switch. Off means no fetch, no game writes and `503` on guess/stats/refresh. |
| `WORDLE_SOURCE_URL` | `https://www.nytimes.com/svc/wordle/v2/{date}.json` | JSON source template; `{date}` is `YYYY-MM-DD`. |
| `WORDLE_TIMEZONE` | `Africa/Tripoli` | Timezone used to resolve "today" and the refresh hour. |
| `WORDLE_REFRESH_HOUR` | unset | Optional `0`-`23` local hour for the in-process pre-warm. |

The source must return a JSON object with `solution` (exactly five uppercase
A-Z letters) and `print_date` equal to the requested date. Simulated or
fixture sources are used in tests; CI makes no live network call. Malformed,
missing or mismatched responses produce `WORDLE_UNAVAILABLE`; the service never
falls back to another date or a fabricated answer.

## Fetch-once guarantee

`wordle_puzzles.puzzle_date` is the primary key. `ensure_today()` inserts a
short-lived claim row (`answer` is null) with `INSERT ... ON CONFLICT DO
NOTHING`; only the claimant calls the source. Concurrent requests poll the
stored row for a bounded time instead of calling out. An expired claim can be
reclaimed atomically, and failed attempts are recorded through
`last_attempt_at` plus a retry cooldown. The database uniqueness guarantee, not
the scheduler, is authoritative.

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

## Migration

`migrations/versions/0011_wordle.py` adds `wordle_puzzles`, `wordle_games` and
`wordle_guesses` with owner/date indexes, the unique `(owner_id, puzzle_date)`
game key, the unique `(game_id, attempt)` guess order and unique `request_id`.
Downgrade drops only the Wordle tables. PostgreSQL deployments also get RLS and
revoked `anon`/`authenticated` grants on the new tables.

## Voice play (#110)

The web client adds a deterministic command adapter that consumes **finalized
user transcripts only** (`apps/web/src/lib/wordle/wordleVoice.ts`). Partials and
assistant segments never dispatch. Commands are parsed by
`wordleCommands.ts`; there is no second board and no second game state — the
adapter drives the same `createWordleGame` store the stage renders.

| Utterance | Behavior |
|---|---|
| "Play Wordle" / "Play today's Wordle" | Opens the stage and resumes today's game once (`GET /v1/wordle/today`). |
| "Guess CRANE" / "C R A N E" | Normalizes to five A–Z letters and posts once with a replay-safe `request_id`. |
| "How many guesses left?" | Reads the shared server state. |
| "What did I guess?" | Reads the stored guesses; never the answer. |
| "Quit Wordle" / "Back to chat" | Closes the stage; the voice session, mic and transcript are untouched. |
| "Reveal the answer" | Asks for explicit confirmation. The answer is spoken only if the server has released it at terminal; while the game is live the request is refused. |

Design notes:

- A `request_id` is `scope-date-WORD`, where `scope` is a per-tab random id in
  `sessionStorage`. Retrying the same word (network failure or a repeated
  segment) replays the server row; an already-guessed word is rejected locally
  without a request.
- Segment ids are deduplicated per epoch, so a replayed finalized event cannot
  create a second guess or transition.
- Ambiguous or malformed speech is spoken back as a clarification and mutates
  nothing. Exact commands never generate a conversational turn, and the adapter
  has no `sendText` dependency.
- The answer lives only in the shared store's terminal state. The adapter's
  `releasedAnswer()` helper returns it only for `won`/`lost`, and the stage and
  voice reply read from that helper; an answer supplied early by a buggy server
  is dropped by `normalizeToday`.
- Replies are delivered through the injected `speak` sink, which currently
  mirrors them into the stage's single polite live region. Audible delivery via
  the agent voice requires a non-LLM `say` channel in
  `apps/web/src/lib/audio/voice-runtime.ts` (out of scope here); until then the
  existing agent TTS remains the audible path and is not fed the answer.

The command path is covered by `apps/web/src/lib/wordle/wordleCommands.test.ts`,
`wordleVoice.test.ts` and the mocked-contract Playwright spec
`apps/web/test/wordle-voice.spec.ts`. A DEV-only `window.__sarjyWordleVoice`
seam injects finalized/partial transcript events in browser tests without a
LiveKit room; it is compiled out of production builds.
