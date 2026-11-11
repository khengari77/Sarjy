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

Expected provider spend for one full pass: roughly 10–20 LLM requests and a few hundred ElevenLabs
characters, subject to the existing `$1/day` and `500 characters/day` guards. If a
`DAILY_BUDGET_EXHAUSTED` or `BUDGET_SERVICE_UNAVAILABLE` error appears, stop and record the stopping
point; do not reset or refund the ledger.

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
- Optional session/cleanup read-back (read-only) for D1.6:

  ```sh
  psql "$DATABASE_URL" -c \
    "SELECT id, status, expires_at, room_cleanup_completed_at
       FROM voice_sessions ORDER BY expires_at DESC LIMIT 5;"
  ```

## D1.3 — Hosted English microphone, audible reply, durable turns

1. Start from a fresh conversation (**New conversation +**) with English selected (the toggle should read
   **العربية**, meaning the current language is English) and a voice chosen.
2. Press **Start call** and grant microphone permission when prompted. Wait for the status line to show
   `listening` (cold starts can take 10–20 seconds).
3. Say: **"Hello, can you hear me? Please reply in one short sentence."**
4. Confirm all three observations:
   - a final English user transcript appears (not partial text that never settles);
   - a relevant spoken reply is audible through the speakers/headphones;
   - no error banner appears.
5. Press **End call**, then reload the page. Confirm the same conversation shows exactly one completed user
   turn and one completed assistant turn, in order.

Pass requires a real spoken utterance and actually hearing the reply. A typed message does not count.

## D1.4 — Weather clarification and an audible factual answer

1. Start a new call (or continue after D1.3) and ask with no city: **"What is the weather?"**
2. Confirm the assistant asks for a city and does not invent weather.
3. Answer: **"Tripoli, Libya."**
4. Confirm the spoken answer names Tripoli and a temperature.
5. Expand the `get_current_weather · Open-Meteo` panel under the conversation and confirm:
   - `city` is `Tripoli`, `country` is `Libya`;
   - `current.temperature_2m` matches the spoken temperature;
   - `source` is `Open-Meteo` and the timezone is `Africa/Tripoli`.
6. End the call. Record the assistant text and the matching tool temperature in the evidence record.

## D1.5 — Typed/voice history continuity

1. Start a call and say a unique, harmless fact: **"My lucky number is 4172."**
2. Without ending the call, type: **"What is my lucky number?"** and press **Send**.
3. Confirm the typed answer repeats 4172 from the spoken turn.
4. Say: **"Multiply it by two."**
5. Confirm the spoken answer uses 8344, proving the voice turn also inherited the typed context.
6. End the call and reload. Confirm both modes appear once each, in order, in one conversation.

## D1.6 — Mute, interruption, stale output, reconnect, refresh, end, microphone denial

Run each scenario as a separate call and end the call between scenarios. Record the observed browser status
line, transcript/audio behaviour, and the session row where noted.

### Mute

1. Start a call. The control initially reads **Enable microphone** because the call starts muted; press it,
   say "Can you confirm you can hear me?", and confirm a transcript appears.
2. Press **Mute**, then say "This sentence must not be transcribed."
3. Confirm no new user transcript appears for the muted sentence. Unmute and speak again to confirm recovery.

### Interruption and stale output

1. Ask a question with a long answer: **"Describe in detail how you would plan a two-day trip."**
2. While the agent is speaking, press **Stop / إيقاف** (or speak over it) and confirm audio stops promptly.
3. Wait at least two seconds. Confirm no abandoned sentence arrives as new audio or as a late transcript.
4. Ask a short follow-up and confirm the conversation continues normally.

### Reconnect

1. Start a call and complete one turn.
2. Disable the network for about five seconds, then re-enable it.
3. Confirm the status moves through `reconnecting` back to a live state, or the app cleanly ends the call
   instead of hanging. In either case, confirm a new call can be started afterwards and history is intact.

### Refresh mid-call

1. Start a call, complete one turn, and reload the page mid-call.
2. Confirm no orphaned or duplicate turns appear, the conversation history is still readable, and a new call
   starts normally. Confirm the previous session ends rather than staying `active` (optional SQL read-back).

### End and cleanup

1. Start a call, then press **End call**.
2. Confirm the status returns to `idle` and the session row is `ended`.
3. Within a few seconds, confirm `room_cleanup_completed_at` is set (optional SQL read-back). Repeating the
   end request must return `cleanup: already_complete` without another provider delete; if you cannot issue
   that request without exposing credentials, note it as not checked rather than guessing.

### Microphone denial and recovery

1. In browser site settings, block the microphone for the app, reload, and press **Start call**.
2. Confirm the UI reports `MICROPHONE_UNAVAILABLE` (or an equivalent actionable error) and offers a way back.
3. Allow the microphone in site settings, then press **Start call** again (or **Enable microphone** if a call
   is still live) and confirm audio capture recovers without a manual database or room teardown.

## D1.7 — Record revisions, input mode and guarded usage

Fill the [acceptance record](day1-acceptance-evidence.md) with:

- source and deployment revisions, worker version, database revision;
- input mode per gate: `human_microphone`, `typed_with_audio`, or `text_only`;
- what was observed (transcript, audible reply, tool values, state transitions, cleanup state);
- ledger values before/after and the delta, with the read-only command used;
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
