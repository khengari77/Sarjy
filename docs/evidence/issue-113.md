# Issue #113 acceptance evidence record

Status: **harness delivered; the gate is not green on the current integration base.**

This record describes what the runner proved and what it could not. It is not an acceptance claim for
unmerged features. The last recorded harness run executed against
`integration/living-cloud` at `6f05d3a53d486f96f41ac40439a4112effce78ae`. The harness commit adds only tests,
scripts and documentation; re-run `bash scripts/run_evidence.sh` to key artifacts to any later SHA.

## Recorded result (base `6f05d3a`)

Command:

```sh
SARJY_RESOURCE_PROFILE=1 bash scripts/run_evidence.sh
```

Result: **11 passed, 1 failed, 4 skipped** (6.8 minutes, one worker, serialized on
`/tmp/sarjy-browser-tests.lock`).

Artifacts (ignored, not committed):
`test-results/evidence/6f05d3a53d486f96f41ac40439a4112effce78ae/`, with `index.md`, `report.json`,
`screens/` and the measured JSON files.

| Evidence | Result on this base |
| --- | --- |
| 1440×900 and 390×844 screenshots in light and dark | passed (`theme-desktop-*.png`, `theme-mobile-*.png`) |
| Measured contrast, light and dark | passed (`contrast-light.json`, `contrast-dark.json`) |
| Seven orb states in both themes | passed, fixture-driven via DEV `?phase`/`?overlay` (`orb-*.png`) |
| Reduced-motion static, filled, non-animating orb | passed both themes |
| Tablet/mobile start with both drawers closed | passed |
| Desktop starts left-open/right-closed | **failed**: base opens both docks; fixed by #120 |
| Drawer open/close without a session POST or orb rebuild | passed |
| Keyboard-only path to the orb, visible focus, one session POST | passed |
| Theme toggle, persistence, Escape and focus return | skipped: control/handlers pending #120 |
| Wordle one-fetch and no pre-terminal answer | skipped: #108 data (PR #118) and #109 UI (PR #125) absent |
| Voice-switch audible ACK | unrun: needs #112 (PR #124) and a real provider |
| Five-minute idle resource profile | passed, no growth beyond tolerance |

## Pending dependencies

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

## Resource profile

The five-minute idle run sampled every 15 seconds and reported an `ok: true` verdict with zero violations.
The report records the browser, viewport, hardware concurrency and device memory. Measured counters include
live requestAnimationFrame handles, live observers, audio contexts, WebGL contexts created per canvas, DOM
nodes, DOM listeners and detached nodes (`DOM.getDetachedDomNodes`).

Limits: headless Chromium only; no real provider audio/GPU traffic; WebGL contexts are not individual GPU
buffers; idle mode does not exercise streaming audio. A listening run is available with
`SARJY_RESOURCE_PROFILE_STATE=listening` but still uses the app runtime factory, not LiveKit.

## Wordle and voice-switch notes

- Wordle is not present on this base. The one-fetch/no-pre-terminal-answer proof must come from #108 tests
  (PR #118); no Wordle acceptance is claimed here.
- An audible voice-switch ACK cannot be proven by text tests. It remains unrun until #112 (PR #124) is merged
  and a real LiveKit/ElevenLabs session is available within the shared budget.

## Reviewer notes

- Orb state screenshots are generated from the app's documented DEV fixtures (`?phase=`, `?overlay=`), not
  from a live runtime. Runtime-transition evidence is separate and still pending #121/#123.
- When #120 is absent, the runner forces the documented `data-theme` attribute for screenshots and measured
  contrast, and records `"themeApplication": "forced-attribute"` in the JSON. This is disclosed, not hidden.
- A failed row stays in the index with its error. Skips carry a `pending-dependency` annotation. Neither is
  acceptance.
