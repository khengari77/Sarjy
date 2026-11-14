# Behavioral regression gates

Tracking: #130. Shared baseline: #102. No hosted or paid-provider acceptance is implied.

Required checks retain unit/browser/proxy suites and add the existing offline deadline suite and smoke-option checks. PostgreSQL jobs share both database URLs and preflight their configuration and a single migration head. Required integration suites must not silently skip.

## Feature-owner handoff

These are integration prerequisites, not optional suites to skip when absent:

- #115/#116: old release pending while replacement End/disconnect occurs; cancellation stops local media before backend release. Use real controllers and deferred transport/HTTP boundaries.
- #85: real worker/API/database with fake embedder: deliver a question, reply `remember it`, acknowledge exactly one fact, never propose/store a pronoun. Drive prompt delivery rather than arming private state.
- #118: two PostgreSQL sessions on both existing/new-game paths, terminal guess guard, and one-connection pool available during polling sleep.
- #120: live fixture switch/create confirmation remains operable at 390/900px, Mute/End accessible, both-dock Escape at desktop, blocked-storage fallback.

Authors add tests with their fixes in their existing PRs. Coordinator reviews exact heads, then integrates; new commits invalidate prior approvals. #120 targets integration/living-cloud. Migration renumbering of unmerged/unapplied migrations uses ordinary forward commits, not history rewriting.

## Follow-up after prerequisites merge

A separate issue/worktree implements generated state machines (Hypothesis backend, fast-check frontend), PR profile 25 sequences/15 operations, nightly 200/50, and replayable seeds/minimized traces. Scoped mutmut and Stryker/Vitest run nightly/manual with two workers and 30-minute timeout; narrow reviewed equivalent-mutant exclusions only. Tool versions must be locked without upgrading existing SDK/test runners. Feature-dependent models remain explicitly pending until their production paths land.

## Smoke tooling

`pnpm --dir apps/web test:smoke-setup` checks argument validation without a browser, credentials, recordings or provider calls. `node apps/web/scripts/live-smoke.mjs --check-options [--locale=en]` prints supported selectors and exits. English is currently the only supported UI locale. The live command remains opt-in and needs the existing shared spending guards and explicit authorization. Microphone mode fails before browser startup until an English recording fixture is qualified; the historical fixture is Arabic. Deadline tests use only the fake upstream. Serialize local browser tests under the existing machine-wide lock.
