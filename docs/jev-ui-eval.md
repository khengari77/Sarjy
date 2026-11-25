# Jev UI-command evaluation (live, 2026-11-25)

How often does the classifier behind the exact-phrase grammar act on a sentence that is not a command?

Run: `JEV_API_KEY=... PYTHONPATH=apps/api uv run python scripts/jev_ui_eval.py --out docs/data/jev-ui-eval.json`
Code: `main` at `f48dc97`. Raw per-case results: [`docs/data/jev-ui-eval.json`](data/jev-ui-eval.json). The fixture is
`tests/fixtures/jev_ui_live_cases.json`: 70 cases, of which 30 are figurative or reported-speech negatives added for
this evaluation (for example "You're pushing me to my dark mode", "I'm going dark on social media for a week").

## Result

| | n | correct |
|---|---|---|
| All cases | 70 | 68 |
| Negatives (expected: no command) | 46 | 45 |
| Figurative negatives added for this run | 30 | 29 |
| Real commands, by family | 22 | 21 |
| Compound ("dark mode and hide captions") | 2 | 2 |

- Errors: 0. The run completed (70 of 70 cases).
- **False triggers: 1 of 46 negatives (1 of 30 figurative).** Missed commands: 1 of 22. Wrong action: 0.
- Latency per call: p50 322 ms, p95 936 ms, max 2,322 ms (includes the network round trip from this machine).

## The two mistakes

| Sentence | Wanted | Got | Why |
|---|---|---|---|
| "Slow down, life is too fast" | no command | speed slower | `route` answered ui_command at only 0.54, but `family` (speed, 0.93) and `detail` (slower, 0.95) were both confident. The gate acts on family and detail agreeing and only lets a confident `conversation` route veto. |
| "Show me what you're saying" | captions on | no command | `family` (captions) was 0.83, just under the 0.85 threshold. |

The reviewers' own example, "You're pushing me to my dark mode", did not trigger.

## Decision

The target set in #277 for this phase was zero false triggers on the figurative set. It is **not met: 1 of 30.**
It is not fixed here, on purpose:

- One trip in 70 cases is not evidence to retune a threshold on. Requiring a stronger `route` would also reject
  polite real requests: the gate's own comment records that `route` is the weakest answer for "Can you make it dark?"
  and similar, so tightening it trades false triggers for missed commands. A change should be judged on a larger set.
- The cost of this particular mistake is small and visible: speaking speed drops one 0.1 step, and the user can say
  "speak faster" or "undo" (P5 makes undo universal).
- The exact-phrase grammar in front of it is unaffected: it matches whole utterances only, by test (#275).

Next step if this matters: grow the figurative set past a few hundred cases before moving any threshold, and report the
false-trigger and missed-command rates together.
