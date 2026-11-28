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
| 20 turns, 20 memories | 55 ms | 38 KB |
| 100 turns, 200 memories | 80-95 ms | 115 KB |

For the long conversation (mean ms per turn; phases overlap slightly):

| Phase | ms | Notes |
|---|---|---|
| `read_context_inputs` | 38-45 | about 300 ORM objects built (100 turns, 200 memories), tool join; this run has no exclusion, so the recursive exclusion queries are not included (they only add) |
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
| `snapshot_request` (validate + hash), up to twice per turn (early-reply path) | 4.1 each |
| serialize the request for the bounds check | 1.5 |

About 10-12 ms per turn for these steps. This is a lower bound: the worker also dumps and re-validates the request (`_with_continuation`) on every model turn, not measured here.

## What this says

- Python compute is about 80-95 ms of the 777 ms turn start (10-12%); the rest is database round trips and network.
  Most of the gain is in the API and database hops, as expected, but a few tens of ms of the Python are easy to recover (the items below overlap, so do not add them up).
- Cheap, safe wins (not done here): return the response with a direct `json.dumps` (or `orjson`) instead of FastAPI's
  `jsonable_encoder` (-10 ms); stop echoing the prompt three times in the stored/returned context (-5-10 ms API,
  -2.5 ms worker decode, less on the wire); build the worker's request once per turn instead of twice (-4 ms); load
  memories as columns instead of ORM objects (-15-25 ms; see `read_context_inputs`).
- Anything bigger needs fewer or parallel database round trips (#224 section C) or the design changes in #225-#227.

## Measuring call start

These marks are measurement only: they do not change a request, response or user-visible behaviour.

Enable them in the browser with `localStorage.setItem('sarjy:call-timing', '1')`, then press the call button
and read the single `[Sarjy call-start]` `console.info` line (or `window.__sarjyCallStart`). Development
builds (`import.meta.env.DEV`) always record; production records only with that flag. Remove it with
`localStorage.removeItem('sarjy:call-timing')`.

Marks are milliseconds since `press`; the `steps` object is the delta between consecutive marks.

`press` is marked when `connect()` starts, so it also covers a reconnect after teardown and the first typed
message that opens a call without the microphone.

| Mark | Meaning |
|---|---|
| `press` | `connect()` started |
| `conversation_ready` | `ensureConversation` returned (or an existing conversation was used) |
| `mic_ready` | microphone permission resolved (omitted when the call starts muted) |
| `session_request_start` | `POST /v1/voice/sessions` is about to be sent |
| `session_response` | the session response arrived |
| `room_joined` | `room.connect` resolved (the WebRTC join completed) |
| `agent_state_first` | `waitForAgent` resolved after observing the first agent state in `listening`/`thinking`/`speaking` |
| `room_connected` | `runtime.connect` returned, so the room is joined and the agent is live |
| `mic_started` | the local microphone track started publishing (omitted when muted) |
| `first_agent_audio` | the remote agent audio track subscribed (present only if it happened before the report) |

The API logs `voice_session_created` at info level with the values in the message as compact JSON, for example
`INFO sarjy.voice voice_session_created {"commit_ms":12,"dispatch_ms":35,"pre_commit_ms":240,"server_ms":290}`.
`pre_commit_ms` is everything from handler entry to just before the session commit: ownership, validation, the
external voice-catalog call, settings read, gate updates and the admission check. `commit_ms` is the session
row commit and `dispatch_ms` is the LiveKit `create_dispatch` call. It appears in CranL API logs.

The worker logs `call_start_stages` at info level with milliseconds since the job was accepted, with the stage
values in the message as JSON, for example
`call_start_stages {"bootstrap_done":12,"greeting_done":940,"participant_joined":820,"session_started":34}`.
The keys are `bootstrap_done`, `session_started`, `participant_joined`, `greeting_say_called` and
`greeting_done` (after `wait_for_playout`). `greeting_say_called` is recorded when `session.say` is scheduled,
not when audio starts. The line is logged when the greeting path runs and is not logged if the job fails before
the greeting starts. It appears in the LiveKit Cloud worker logs.
