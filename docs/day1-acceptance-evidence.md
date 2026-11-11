# Day 1 human acceptance record

Status: **pending**. This file is a scaffold, not evidence. Fill it only while performing
[the runbook](day1-acceptance-runbook.md) with a real microphone and a real browser. Never pre-fill it with
typed-input, synthetic-audio, fake-microphone or expected results, and never commit raw audio, recordings,
cookies, tokens, credentials or database exports.

Related: [hosted operational verification](day1-hosted-verification.md) (typed-input, lifecycle and
deployment facts that are already recorded). This file replaces the "Remaining gates" checklist only when
all of them have passed.

## Run metadata

| Field | Value |
|---|---|
| Source revision | `<git sha under test>` |
| API deployment | `<deployment id / branch>` |
| Web deployment | `<deployment id / branch>` |
| Worker version | `<version>` |
| Database revision | `<alembic revision>` |
| Browser / OS | `<name and version>` |
| Audio path | `<headphones / speakers>` |
| Run started (UTC) | `<timestamp>` |
| Run finished (UTC) | `<timestamp>` |
| Operator | `<coordinator name>` |

## Gate results

| Gate | Result | Input mode | Observed | Evidence file |
|---|---|---|---|---|
| D1.3 microphone, audible reply, durable turns | pending | human_microphone | `<transcript, audible reply, reload order>` | `<screenshot or note>` |
| D1.4 weather clarification and factual answer | pending | human_microphone | `<clarification, spoken temperature, tool temperature>` | `<screenshot or note>` |
| D1.5 typed/voice continuity | pending | human_microphone + typed | `<4172 and 8344 turn order>` | `<screenshot or note>` |
| D1.6 mute, interruption, reconnect, refresh, end, denial | pending | human_microphone | `<per-scenario state transitions>` | `<screenshots or notes>` |
| D1.7 revisions, input mode, ledger delta | pending | all | `<values recorded below>` | `<ledger snapshot>` |
| D1.8 docs reconciled, release marker | pending | n/a | `<docs updated, marker sha>` | `<commit sha>` |

## D1.6 scenario detail

| Scenario | Pass | Observed |
|---|---|---|
| Mute stops publication and recovers | pending | `<no transcript while muted; recovery confirmed>` |
| Interruption stops abandoned audio | pending | `<audio stopped; no late transcript/audio>` |
| Reconnect recovers or ends cleanly | pending | `<status transitions; next call works>` |
| Refresh mid-call leaves no orphans | pending | `<turn count, session status>` |
| End returns idle and cleanup completes | pending | `<session status, room_cleanup_completed_at>` |
| Microphone denial offers recovery | pending | `<error code, recovery step>` |

## Ledger snapshot

Read-only before and after the pass. Values are reserved units, not vendor invoices.

| Provider | Before | After | Delta |
|---|---|---|---|
| openai | `<units>` | `<units>` | `<delta>` |
| elevenlabs | `<characters>` | `<characters>` | `<delta>` |

Command used (hosted `DATABASE_URL` injected; no writes):

```sh
psql "$DATABASE_URL" -c \
  "SELECT day, provider, reserved_units FROM development_budgets ORDER BY day, provider;"
```

## Artifacts

Ignored files under `artifacts/deployment/day1-human-acceptance/`:

- `<screenshot-d1.3.png>` — transcript, audible reply path and post-reload turns
- `<screenshot-d1.4.png>` — clarification, spoken answer and expanded Open-Meteo tool result
- `<screenshot-d1.5.png>` — typed/voice interleaving after reload
- `<screenshot-d1.6-*.png>` — mute, interruption, reconnect, refresh, end, denial
- `<ledger-before-after.txt>` — copy of the two read-only query outputs
- `<notes.md>` — free-form observations, failures and stopping points

State explicitly if any artifact is missing or if a scenario was not run.

## Machine-readable companion (optional)

If a JSON summary is written under the ignored artifacts directory, use this shape so the coordinator can
compare runs. Do not put it in git.

```json
{
  "source_revision": null,
  "input_mode": "human_microphone",
  "browser": null,
  "gates": {
    "D1.3": {"pass": null, "transcript": null, "audible": null, "reload_turns": null},
    "D1.4": {"pass": null, "clarification": null, "spoken_temperature": null, "tool_temperature": null},
    "D1.5": {"pass": null, "typed_answer": null, "voice_answer": null, "order": null},
    "D1.6": {
      "mute": null,
      "interruption": null,
      "reconnect": null,
      "refresh": null,
      "end_cleanup": null,
      "denial_recovery": null
    }
  },
  "ledger": {"before": null, "after": null},
  "notes": null
}
```

## Blockers and limitations

- D1.3, the voice half of D1.5, and the audio/interruption half of D1.6 cannot pass without a human at a real
  microphone; automated browser runs cannot substitute.
- A typed-input weather check already recorded in [hosted verification](day1-hosted-verification.md) does
  not satisfy D1.4; the live audible answer still has to be heard.
- Hosted revisions can drift after this run. Record the exact revisions used and re-run any gate that was
  tested against a different revision.
- No release marker may be created while any gate above remains pending.
