# Day 1 human acceptance runbook (D1.3–D1.8)

Coordinator-owned procedure for the gates that the automated and typed-input checks in
[hosted verification](day1-hosted-verification.md) cannot satisfy. Run this at a real browser with a
real microphone; only the observations recorded here can close D1.3, D1.5 (voice side), and D1.6.

Do not treat text-only, synthetic-audio, fake-microphone or replayed-recording results as evidence for
these gates. D1.4 may use typed input for the weather tool check, but its audible answer must still be
heard live. D1.7 and D1.8 are recorded only from the results of this run.

## Preconditions

- Hosted revision under test: `4a80697` (API, web) with database `0004_voice_cleanup`, or a later
  coordinator-approved revision. Record the exact revision used; do not assume.
- Protected development URL with the coordinator's credentials. HTTP Basic auth prompts on first load.
- One selected English voice: press **Load voices / Search**, then choose a voice in **Choose voice**.
  The call button posts a null voice otherwise and returns `VOICE_REQUIRED`.
- Headphones, quiet room, microphone allowed for the site. Headphones avoid the agent's own audio
  re-entering the microphone and skewing interruption/turn-taking results.
- Browser devtools available for the optional state read-back. Never copy tokens, cookies, passwords or
  `Authorization` headers into an evidence file or screenshot.
- No other active call, and no second browser tab holding a session. The application allows one active
  session per identity and two globally; a stale session returns `429`.
- Session cap is ten minutes; the worker stops 60 seconds after the browser participant leaves.
- The microphone control reflects the real call state: a successful **Start call** enables microphone
  capture and the control reads **Mute**. Typed input sent with no live call (`connect(false)`) creates a
  session with capture off, so the control reads **Enable microphone** until it is pressed. Do not add an
  extra enable step after a successful **Start call**.

Expected provider spend for one full pass: roughly 10–20 LLM requests and a few hundred ElevenLabs
characters, subject to the existing `$1/day` and `500 characters/day` guards. If a
`DAILY_BUDGET_EXHAUSTED` or `BUDGET_SERVICE_UNAVAILABLE` error appears, stop and record the stopping
point; do not reset or refund the ledger.

## Session reservations (read before starting)

Each admitted `POST /v1/voice/sessions` consumes one reservation permanently, even if the call later
fails, times out or is abandoned. A `429` admission rejection consumes none. The defaults are
`demo_calls_per_identity=3` counted **all-time per owner** and `demo_session_budget=80` counted per
calendar month, with at most one active session per identity and two globally. Earlier hosted checks may
already have consumed part of the identity allowance, so check before running.

Read-only capacity checks (owner id is the coordinator's identity; do not write to these tables):

```sh
psql "$DATABASE_URL" -c \
  "SELECT owner_id, count(*) FROM voice_sessions WHERE owner_id = '<owner-id>' GROUP BY owner_id;"
psql "$DATABASE_URL" -c \
  "SELECT count(*) FROM voice_sessions
     WHERE expires_at::timestamptz >=
       (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC');"
psql "$DATABASE_URL" -c \
  "SELECT count(*) FROM voice_sessions WHERE status IN ('starting', 'active');"
```

`remaining = 3 - identity_used`; also require `month_used < 80` and `active_now = 0` before starting.
The month figure counts sessions whose expiry is at or after the current UTC month boundary, matching
the API's lower-bound check without adding an upper bound. `expires_at` is stored as text, so the explicit
cast is required in PostgreSQL. A session started just before a month boundary can have an expiry in the
next month; record the observed values. The query is independent of the database session's timezone.

The procedure needs at most three reservations, allocated as follows. Do not start a fourth.

| Reservation | Scenarios | Notes |
|---|---|---|
| S1 | D1.3, D1.4, D1.5, end/cleanup, post-end reload | One live call and one conversation; the reload after **End call** verifies the durable turns and the tool panel. |
| S2 | D1.6 microphone denial and recovery, then mute and interruption | Denial is attempted first; if it leaves a live session, recovery through **Enable microphone** reuses it. |
| S3 | D1.6 reconnect, then refresh mid-call | Reconnect first; if the session survives, reload to exercise refresh. |

Allocate by remaining allowance:

| Remaining | Run | Leave pending |
|---|---|---|
| 3 | S1, S2, S3 | nothing |
| 2 | S1, S2 | S3 (reconnect, refresh) |
| 1 | S1 | D1.6 scenarios |
| 0 | none | every gate |

Rules that keep this honest:

- Never reset counters, rotate identities, create a second owner, or raise a limit to fit the procedure.
- A failed start (`AGENT_START_TIMEOUT`, provider error, no audible output) has consumed its reservation.
  Record the failure and move on; do not retry blindly and do not start extra sessions to "make up" for it.
- Reconnect and refresh are only tested while a session is live. If reconnect ends the call, refresh
  mid-call cannot run in that reservation; leave it pending rather than opening a fourth session.
- A typed **Send** during a live call reuses that session and consumes nothing. A typed **Send** with no
  live call starts a session and consumes a reservation; use it only if the allowance covers it.
- The ten-minute session cap and the daily budget guards stay in force. End the call before the cap; do
  not extend the TTL, and do not reset the ledger to continue.

## Evidence rules

- Commit no credentials, cookies, tokens, raw audio, recordings, database exports or full-page screenshots
  that include the password prompt.
- Store raw-ish artifacts under the ignored `artifacts/deployment/day1-human-acceptance/` directory.
- Record sanitized results in [the acceptance record](day1-acceptance-evidence.md) and reference the
  artifact filenames.
- Capture the ledger read-only before and after the whole pass:

  ```sh
  psql "$DATABASE_URL" -c \
    "SELECT day, provider, reserved_units FROM development_budgets ORDER BY day, provider;"
  ```

  Run this in a process that has the hosted `DATABASE_URL` injected; do not write to the ledger and do not
  create a reservation merely to test that it did not decrease. Reservations are guards, not invoices.
- Required session/cleanup read-back (read-only) for D1.6. Capture each reservation's session ID from the
  start response without copying its token. Query that exact ID, not just the latest sessions:

  ```sh
  psql "$DATABASE_URL" -c \
    "SELECT id, status, expires_at, room_cleanup_completed_at
       FROM voice_sessions WHERE id = '<session-id>';"
  ```

  Record the session ID, observed status, cleanup timestamp and observation time in the acceptance
  record, with a sanitized artifact reference. Missing access, no matching row, `active`/`starting`
  status or a null cleanup timestamp leaves cleanup pending or failed, never passed. Do not manually
  end or reconcile an orphaned session before recording the failure; a repaired row does not prove
  that the tested end or refresh path worked.

## D1.3 — Hosted English microphone, audible reply, durable turns

This is the first part of reservation **S1**. Keep the call live afterwards for D1.4 and D1.5; the
post-end reload at the end of D1.5 covers this gate's durable-turn check.

1. Start from a fresh conversation (**New conversation +**) with English selected (the toggle should read
   **العربية**, meaning the current language is English) and a voice chosen.
2. Press **Start call** and grant microphone permission when prompted. A successful start enables capture,
   so the microphone control reads **Mute**. Wait for the status line to show `listening` (cold starts can
   take 10–20 seconds).
3. Say: **"Hello, can you hear me? Please reply in one short sentence."**
4. Confirm all three observations:
   - a final English user transcript appears (not partial text that never settles);
   - a relevant spoken reply is audible through the speakers/headphones;
   - no error banner appears.

Pass requires a real spoken utterance and actually hearing the reply. A typed message does not count.

## D1.4 — Weather clarification and an audible factual answer

Continue in the same live call (reservation **S1**) after the D1.3 turn.

1. Ask with no city: **"What is the weather?"**
2. Confirm the assistant asks for a city and does not invent weather.
3. Answer: **"Tripoli, Libya."**
4. Confirm the spoken answer names Tripoli and a temperature.
5. Expand the `get_current_weather · Open-Meteo` panel under the conversation and confirm:
   - `city` is `Tripoli`, `country` is `Libya`;
   - `current.temperature_2m` matches the spoken temperature;
   - `source` is `Open-Meteo` and the timezone is `Africa/Tripoli`.
6. Record the assistant text and the matching tool temperature in the evidence record; leave the call live.

## D1.5 — Typed/voice history continuity

Continue in the same live call (reservation **S1**). The typed message must be sent while the call is live;
a typed message with no live call would start a new session and consume another reservation.

1. Say a unique, harmless fact: **"My lucky number is 4172."**
2. Without ending the call, type: **"What is my lucky number?"** and press **Send**. This reuses the live
   session.
3. Confirm the typed answer repeats 4172 from the spoken turn.
4. Say: **"Multiply it by two."**
5. Confirm the spoken answer uses 8344, proving the voice turn also inherited the typed context.
6. Press **End call**, then reload the page. Confirm the D1.3 user/assistant pair, the weather turns and
   both continuity turns all appear once, in order, in one conversation, and the persisted tool panel
   still shows the Tripoli result. This proves history persistence. Complete the separate D1.6 end and
   cleanup read-back for S1 before marking its lifecycle check passed.

## D1.6 — Mute, interruption, stale output, reconnect, refresh, end, microphone denial

S1's history reload does not establish session termination or room cleanup. Verify S1 using the end and
cleanup procedure below. The remaining scenarios use reservations **S2** and **S3** as allocated above.
Record the observed browser status line, transcript/audio behaviour, and exact session row.

### S2 — Microphone denial and recovery

1. In browser site settings, block the microphone for the app, then press **Start call**.
2. Confirm the UI reports `MICROPHONE_UNAVAILABLE` (or an equivalent actionable error).
3. If the call is still live (the control reads **Enable microphone** and the status is not `idle`), allow
   the microphone in site settings and press **Enable microphone**. This recovers the same session without
   a reload or another reservation. Confirm capture works by speaking a short sentence and seeing a
   transcript.
4. If denial ended the session instead, record the denial result and stop the S2 scenarios; leave mute and
   interruption pending rather than opening another session.

### S2 — Mute

1. In the recovered live call, confirm the control reads **Mute**, meaning capture is enabled.
2. Say "Can you confirm you can hear me?" and confirm a transcript appears.
3. Press **Mute**, then say "This sentence must not be transcribed."
4. Confirm no new user transcript appears for the muted sentence.
5. Press **Enable microphone**, speak again, and confirm a new transcript appears (capture restored).

### S2 — Interruption and stale output

1. Ask a short, bounded prompt so synthesis stays cheap: **"Count slowly from one to twenty."**
2. While the agent is speaking, press **Stop / إيقاف** (or speak over it) and confirm audio stops promptly.
3. Wait at least two seconds. Confirm no abandoned sentence arrives as new audio or as a late transcript,
   and that stopping does not require more than the prompt's own synthesis.
4. Ask a short follow-up and confirm the conversation continues normally, then press **End call**.

### S3 — Reconnect and refresh mid-call

1. Start a call and complete one turn.
2. Disable the network for about five seconds, then re-enable it.
3. Confirm the status moves through `reconnecting` back to a live state. If the app instead ends the call
   cleanly, record that and leave refresh mid-call pending; do not open a fourth session.
4. If the session is live again, reload the page mid-call. Confirm no orphaned or duplicate turns appear,
   the conversation history is still readable, and the browser returns to `idle`. Use the required
   read-back for S3's exact session ID to confirm `status = 'ended'` and a non-null
   `room_cleanup_completed_at`. Allow up to 90 seconds for the documented 60-second worker departure
   grace period and cleanup. Record the elapsed time; if confirmation is missing, record the failure
   and leave refresh/cleanup unpassed. History reload alone is insufficient.
5. Do not start another call to test connectivity unless the read-only check at the end of S3 shows a
   remaining reservation; otherwise record "new call after recovery: pending".

### End and cleanup

1. After **End call** in S1 and S2 (when run), confirm the browser returns to `idle`. For each exact session
   ID, use the required read-back to confirm `status = 'ended'` and a non-null
   `room_cleanup_completed_at`. Observe for at most 30 seconds and record the elapsed time and evidence
   reference. Missing confirmation leaves the lifecycle check unpassed even if history reload succeeds.
2. Repeated end must return `cleanup: already_complete` without another provider delete. The existing
   [hosted lifecycle evidence](day1-hosted-verification.md#migration-and-recovery), recorded 11 November
   2026 against source `4a80697a97b25e4f55275d27b31193fa58c93fa8`, may satisfy this idempotency check
   only when the coordinator confirms that revision's evidence applies to the lifecycle implementation
   under test. Record that source and applicability explicitly. Otherwise record idempotency as pending
   until separately verified; do not blindly repeat requests or infer it from a reload.

## D1.7 — Record revisions, input mode and guarded usage

Fill the [acceptance record](day1-acceptance-evidence.md) with:

- source and deployment revisions, worker version, database revision;
- input mode per gate: `human_microphone`, `typed_with_audio`, or `text_only`;
- what was observed (transcript, audible reply, tool values, state transitions, cleanup state);
- ledger values before/after and the delta, with the read-only command used;
- reservations: identity-used and month-used before the run, the intended S1–S3 allocation, the
  reservations actually consumed, and any gate left pending for lack of allowance;
- artifact filenames under `artifacts/deployment/day1-human-acceptance/`.

Distinguish human microphone, synthetic audio and text-only checks explicitly. Never commit raw voice.

## D1.8 — Reconcile docs and release marker

Only after D1.3–D1.7 have passed:

1. Re-read [hosted verification](day1-hosted-verification.md), this runbook, README and the plan note; update
   any obsolete deployment, migration or URL statement.
2. Tick the D1.3–D1.8 boxes in `docs/execution_checklist_v5.md` with dated evidence links.
3. Create the baseline release marker only when every gate has recorded evidence.

Do not create the marker while any gate remains pending.

## Pass/fail summary

| Gate | Result | Observed | Evidence |
|---|---|---|---|
| Reservations (S1–S3, allowance) | pending | | |
| D1.3 microphone, audible reply, durable turns | pending | | |
| D1.4 weather clarification and factual answer | pending | | |
| D1.5 typed/voice continuity | pending | | |
| D1.6 mute, interruption, reconnect, refresh, end, denial | pending | | |
| D1.7 revisions, input mode, ledger delta | pending | | |
| D1.8 docs reconciled, release marker | pending | | |

## Stop conditions

- `DAILY_BUDGET_EXHAUSTED`, `BUDGET_SERVICE_UNAVAILABLE`, `429`, or a missing voice: stop the scenario,
  record where it stopped, and leave the gate unchecked.
- `AGENT_START_TIMEOUT` or no audible output: record it as a failure of the gate, not as a pass.
- A provider or deployment failure that requires a redeploy is coordinator work; do not redeploy mid-run.
