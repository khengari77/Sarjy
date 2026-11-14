# Acceptance evidence

This directory describes how Sarjy records reviewer-facing evidence for the Living Cloud v2.1 gates. It is a
format and a repeatable runner, not a claim that an unmerged feature passed.

## Rules

1. Every artifact belongs to one exact source revision. Pin `SARJY_EVIDENCE_SHA` (or run from a clean checkout)
   and state that SHA in the index. Evidence from a moving branch is not acceptance evidence.
2. Screenshots must come from the running application (`vite dev` plus the real API process). Never use the
   design kit PNGs or the HTML prototype.
3. Large binaries stay out of Git. The runner writes to the already-ignored
   `test-results/evidence/<sha>/` directory. Commit only the text index and measured JSON excerpts that are
   small, redacted and useful for review.
4. A skipped, blocked, fixture-driven or unrun row is not a pass. Name the dependency (issue or PR) and say
   whether a real provider, microphone or hosted deployment was involved.
5. Redact secrets, tokens, cookies, personal data and raw audio. Never attach a session token captured from a
   network response.

## Layout of one run

```text
test-results/evidence/<sha>/
  report.json              Playwright JSON report (raw)
  summary.json             machine-readable status, counts and artifact list
  index.md                 generated human-readable evidence index
  screens/                 viewport/theme/state PNGs
  contrast-light.json      measured colours, ratios, thresholds and failures
  contrast-dark.json
  resource-profile.json    five-minute samples, environment, growth and verdict
  layout-*.json            layout defaults as observed
  orb-states.json          fixture/state provenance for orb screenshots
```

## Commands

```sh
# Visual, layout, theme, drawer, keyboard and contrast evidence (serialized).
bash scripts/run_evidence.sh

# One slice, e.g. layout only.
bash scripts/run_evidence.sh --grep "layout default"

# Five-minute idle resource profile (default 300s, 15s samples).
SARJY_RESOURCE_PROFILE=1 SARJY_RESOURCE_PROFILE_SECONDS=300 \
  bash scripts/run_evidence.sh --grep "resource profile"

# Listening variant (uses the app runtime factory, not a real provider).
SARJY_RESOURCE_PROFILE=1 SARJY_RESOURCE_PROFILE_STATE=listening \
  bash scripts/run_evidence.sh --grep "resource profile"
```

`scripts/run_evidence.sh` pins the SHA, forces Playwright to start its own servers (`CI=1`), serializes on
`flock /tmp/sarjy-browser-tests.lock`, and regenerates `index.md` and `summary.json`.

The evidence suite is deliberately excluded from `pnpm --dir apps/web test:browser`: ordinary CI must not turn
red merely because an unmerged screen is absent. Reviewers run it explicitly and read the index.

## Index format

`index.md` contains, in order:

- exact SHA and generation timestamp;
- the command that produced the run;
- a pass/fail/skipped count;
- one table row per Playwright test with its status, duration and skip/error notes;
- links to screenshots and measured JSON (relative to the ignored artifact directory);
- every skipped or unrun reason;
- the current pending-dependency matrix for the release.

`summary.json` mirrors the table for automation. CI can assert `counts.failed == 0` and inspect
`tests[].annotations` for `pending-dependency` rather than treating skips as success.

## Current pending dependencies

The issue #113 gate spans work that is still in review:

| Gate | Dependency |
| --- | --- |
| Theme control, persistence, Escape and focus return | #120 |
| Theme-aware orb derivation and state contrast | #121 |
| Filled circular cloudy orb and runtime phases | #123 |
| Captions, transcript drawer and history states | #119 |
| Default approved voice, one-tap start | #117 |
| Transparent memory explorer | #122 |
| Resumable onboarding | #126 |
| ACKed mid-session voice switch | #124 |
| Wordle stage UI | #125 |
| Wordle ingestion and game API | #118 |
| Read-only Simple/Developer mode | #127 |

The harness reports these as skipped or failed with the dependency named; no row claims acceptance for a
feature that is not on the base.

## Resource profile method and limits

The helper instruments the page before application code runs:

- live `requestAnimationFrame` handles (scheduled minus cancelled/completed), plus total schedules;
- live `MutationObserver`, `ResizeObserver` and `IntersectionObserver` instances;
- live `AudioContext` instances (created minus closed);
- WebGL contexts created per canvas and `webglcontextlost` events;
- DOM node count, DOM listener count (`Memory.getDOMCounters`) and detached `Node` objects
  (`Runtime.queryObjects` on `Node.prototype`).

Growth is compared between the first and last sample with explicit tolerances. It measures a headless Chromium
run on the recording hardware; the environment block records the user agent, browser version, viewport and
hardware concurrency. It does not prove provider-side audio behaviour, GPU buffer counts or long-lived worker
memory. Those require a real-provider run and must be recorded separately.

## Wordle and voice-switch notes

- Wordle one-fetch and no-pre-terminal-answer evidence comes from the #108 tests (PR #118). Until that data layer
  and the #109 UI (PR #125) are on the base, the evidence suite skips the Wordle row instead of faking a board.
- An audible voice-switch ACK needs #112 (PR #124) and a real LiveKit/ElevenLabs session. Text tests cannot prove
  it; the evidence suite marks this row unrun.
