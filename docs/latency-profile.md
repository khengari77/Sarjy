# Where a turn's Python time goes

Measured with `scripts/profile_turn.py` on 2026-11-24 (this machine, SQLite, no network; run
`uv run python scripts/profile_turn.py --no-profile` for the timers and without `--no-profile` for the cProfile
tables). The point is to separate the Python and ORM work a turn does from the database and network time measured on
the live system (turn start 777 ms median, of which about 12 sequential statements at 30 ms each).

**Read the profiler's percentages as relative cost only.** cProfile inflates code made of many tiny calls: it put
`jsonable_encoder` at about 60 ms per turn, while the unprofiled phase timer says 13-14 ms. Milliseconds below come from
the phase timers.

## API: `POST /internal/agent/turn/start` (handler time, no network, local SQLite)

| Conversation | handler p50 | response |
|---|---|---|
| 20 turns, 20 memories, 10 KB snapshots | 55 ms | 38 KB |
| 100 turns, 200 memories, 30 KB snapshots | 80-95 ms | 115 KB |

For the long conversation (mean ms per turn; phases overlap slightly):

| Phase | ms | Notes |
|---|---|---|
| `read_context_inputs` | 38-45 | about 300 ORM objects built (100 turns, 200 memories), recursive exclusion queries, tool join |
| response encoding (`jsonable_encoder`) | 13-14 | FastAPI walks the 115 KB dict recursively (5,000 calls) before `json.dumps`; the stored context carries the prompt three times |
| `_compile_turn_context` | 5 | |
| `ModelRequest(...)` validation | 4-6 | pydantic validation of messages/memories |
| `_locked_turn_context` | 4 | the single locked read from #233 |
| `validate_assembly` | 0.8 | |
| unaccounted (guard, middleware, framework) | ~15 | |

The single uvicorn process runs all of this on its one event loop, so every millisecond here also delays any other
request in flight, such as the concurrent budget reservation (see #222).

## Worker: Python per turn (median of 25)

| Step | ms |
|---|---|
| decode the 115 KB response JSON | 2.5 |
| `snapshot_request` (validate + hash), run twice per turn | 4.1 each |
| serialize the request for the bounds check | 1.5 |

About 10-12 ms per turn in total.

## What this says

- Python compute is about 80-95 ms of the 777 ms turn start (10-12%); the rest is database round trips and network.
  Most of the gain is in the API and database hops, as expected, but about 30 ms of the Python is easy to recover.
- Cheap, safe wins (not done here): return the response with a direct `json.dumps` (or `orjson`) instead of FastAPI's
  `jsonable_encoder` (-10 ms); stop echoing the prompt three times in the stored/returned context (-5-10 ms API,
  -2.5 ms worker decode, less on the wire); build the worker's request once per turn instead of twice (-4 ms); load
  memories as columns instead of ORM objects (-15-25 ms; see `read_context_inputs`).
- Anything bigger needs fewer or parallel database round trips (#224 section C) or the design changes in #225-#227.
