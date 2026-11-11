# Sarjy Version 5 — execution checklist

See [architecture and specifications](../sarjy_voice_context_studio_plan_v4_arabic_speech.md). Updated 11 November 2026.

Checked means implemented/verified as described, not that every hosted acceptance scenario passed. Unchecked items include verification of existing code. Jev: ~$5 credit user-reported; key pending. DeepSeek: funding confirmation pending. English baseline; Arabic Day 3.

## M0 — completed foundation

### M0.1 — Monorepo, SvelteKit/Svelte 5, FastAPI, LiveKit worker and locked dependencies

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Monorepo, SvelteKit/Svelte 5, FastAPI, LiveKit worker and locked dependencies. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Locked dependencies install and frontend check/build succeed.
- API and worker entrypoints import.

**Verification:** Inspect manifests; use uv sync --locked, pnpm --dir apps/web install --frozen-lockfile, check and build.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.2 — OpenAPI and generated TypeScript contracts

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: OpenAPI and generated TypeScript contracts. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Backend schema and generated TypeScript agree on required fields.
- frontend type checking passes.

**Verification:** Inspect packages/contracts and scripts/export_contracts.py; compare regenerated contracts in a disposable checkout.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.3 — Durable identities, conversations, turns, sessions and budget tables

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Durable identities, conversations, turns, sessions and budget tables. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Conversation/turn records survive a new database connection.
- duplicate request IDs do not create duplicate turns.

**Verification:** Run uv run pytest -q tests/test_identity.py tests/test_worker_callbacks.py tests/test_migrations.py.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.4 — Demo ownership, worker authentication and protected development boundaries

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Demo ownership, worker authentication and protected development boundaries. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Anonymous protected requests fail.
- wrong-owner reads fail.
- forged worker callbacks fail.

**Verification:** Run uv run pytest -q tests/test_development_access.py tests/test_identity.py tests/test_web_access.py after building web.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.5 — Supabase backend-table RLS and public grant restrictions

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Supabase backend-table RLS and public grant restrictions. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Existing backend tables have RLS and anon/authenticated roles cannot read them directly.

**Verification:** Inspect migration 0003 and read hosted pg_tables/grants; do not change grants to perform verification.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.6 — Provider/language validation and approved voice picker

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Provider/language validation and approved voice picker. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Unavailable voices and unsupported language/model combinations fail before dispatch.
- approved choice reaches snapshot.

**Verification:** Run uv run pytest -q tests/test_voices.py tests/test_context.py; inspect browser picker test.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.7 — Open-Meteo integration, ambiguity handling and tool persistence

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Open-Meteo integration, ambiguity handling and tool persistence. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Ambiguous Tripoli requires country clarification.
- a valid location returns real weather.
- callback results persist.

**Verification:** Inspect weather service and worker callback tests; historical standalone weather evidence does not prove hosted spoken weather.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.8 — Pure deterministic context compiler and initial property tests

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Pure deterministic context compiler and initial property tests. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Same immutable inputs produce identical messages/hash.
- excluded IDs are absent and system rules remain.

**Verification:** Run uv run pytest -q tests/test_context.py tests/test_properties.py.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.9 — Persistent OpenAI and ElevenLabs spending reservations

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Persistent OpenAI and ElevenLabs spending reservations. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Concurrent reservations cannot exceed limits.
- restart retains usage.
- OpenAI and ElevenLabs counters are independent.

**Verification:** Run uv run pytest -q tests/test_budget.py tests/test_worker_budget.py.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.10 — CI frontend/backend/browser checks and ignored browser demo videos

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: CI frontend/backend/browser checks and ignored browser demo videos. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- CI includes backend/check/build/browser gates.
- generated videos are ignored and labeled UI-only.

**Verification:** Inspect workflow, recording config and ignored artifacts; use pnpm --dir apps/web test:browser for current UI checks.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.11 — Secret/generated-file ignore rules and private GitHub deployment branches

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Secret/generated-file ignore rules and private GitHub deployment branches. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Credentials, databases, recordings and build artifacts are ignored.
- deployment branches retain correct root Dockerfiles.

**Verification:** Inspect git status, ignore rules and branch Dockerfiles; never print secrets or add ignored artifacts.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.12 — CranL frontend/API deployment and authenticated Supabase persistence verification

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: CranL frontend/API deployment and authenticated Supabase persistence verification. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- API health returns 200.
- anonymous frontend access returns 401.
- authenticated conversation persists after reload.

**Verification:** Use read-only hosted health checks and prior authenticated Chromium evidence; record successful deployment IDs.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.

### M0.13 — Isolated non-root LiveKit worker deployment and observed registration

- [x] Accomplished

**Depends on:** Existing foundation; no new implementation required.

**What to do:** Inspect and retain the existing implementation for this scope: Isolated non-root LiveKit worker deployment and observed registration. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Worker image runs non-root and cloud registration is observed.
- no live speech success is inferred.

**Verification:** Inspect infra/agent.Dockerfile and historical registration evidence; query agent status without revealing credentials.

**Completion evidence:** Historical completion retained; implementation/test references above identify its scope. Add a dated evidence link and revision when re-verifying; hosted speech is not implied.


## Day 1 — baseline acceptance

Execute in order; keep mandatory gates ahead of optional work.

### D1.1 — Review/release pending session-start proxy deadline fix; preserve unrelated working changes

- [x] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Review/release pending session-start proxy deadline fix; preserve unrelated working changes. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Session creation completes within the configured startup deadline.
- normal API requests retain their shorter deadline.
- failure remains actionable.

**Verification:** Review existing proxy diff; check/build web; reproduce delayed dispatch with a fake upstream and run one hosted startup after release.

**Completion evidence:** [Hosted release verification](day1-hosted-verification.md); source 4a80697, database 0004_voice_cleanup, actual session start/end and historical reconciliation passed. Issue #2 closed. Human voice/lifecycle gates remain in D1.3–D1.6.

### D1.2 — Verify failed startup, abandoned sessions, expiry and room cleanup release capacity without resetting quotas

- [x] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify failed startup, abandoned sessions, expiry and room cleanup release capacity without resetting quotas. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Failed startup, abandoned room and expiry leave no active reservation.
- ending twice is safe.
- spending totals never decrease.

**Verification:** Use isolated sessions for each failure, inspect session rows and room existence before/after; assert ledger unchanged except legitimate reservations.

**Completion evidence:** [Hosted release verification](day1-hosted-verification.md); source 4a80697, database 0004_voice_cleanup, actual session start/end and historical reconciliation passed. Issue #2 closed. Human voice/lifecycle gates remain in D1.3–D1.6.

### D1.3 — Verify hosted English microphone input, audible response and durable turns

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify hosted English microphone input, audible response and durable turns. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Real microphone speech yields final English transcript and relevant audible response.
- reload shows completed user/assistant turns.

**Verification:** Perform one short authenticated hosted conversation, end it and reload; text-only or simulated microphone evidence cannot satisfy this gate. Procedure: [Day 1 human acceptance runbook](day1-acceptance-runbook.md); record: [pending evidence](day1-acceptance-evidence.md).

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D1.4 — Verify real weather request, city clarification and spoken factual result

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify real weather request, city clarification and spoken factual result. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Missing city prompts clarification.
- explicit Tripoli/LY triggers actual Open-Meteo data and an audible factual answer matching tool output.

**Verification:** Ask weather without city, then supply Tripoli, Libya; inspect persisted tool result and response location/temperature. Procedure: [Day 1 human acceptance runbook](day1-acceptance-runbook.md); record: [pending evidence](day1-acceptance-evidence.md).

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D1.5 — Verify typed/voice history continuity

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify typed/voice history continuity. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Typed follow-up uses the preceding spoken fact and vice versa.
- both appear once in the same conversation.

**Verification:** Speak a unique harmless fact, ask about it by text, then speak a follow-up; reload and inspect ordered turns. Procedure: [Day 1 human acceptance runbook](day1-acceptance-runbook.md); record: [pending evidence](day1-acceptance-evidence.md).

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D1.6 — Verify mute, interruption, stale-output rejection, reconnect, refresh, end and microphone denial

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify mute, interruption, stale-output rejection, reconnect, refresh, end and microphone denial. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Mute stops microphone publication.
- interruption stops abandoned output.
- reconnect/refresh/end release or recover state.
- denied permission offers recovery.

**Verification:** Run each scenario separately; record state transitions, room/session cleanup and absence of late stale text/audio. Procedure: [Day 1 human acceptance runbook](day1-acceptance-runbook.md); record: [pending evidence](day1-acceptance-evidence.md).

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D1.7 — Record deployed revisions and guarded usage; human speech acceptance is distinct from text/UI tests

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Record deployed revisions and guarded usage; human speech acceptance is distinct from text/UI tests. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Evidence names code/deployment revisions, input mode, observed result and ledger delta.
- no secret or raw voice enters git.

**Verification:** Record evidence in ignored artifacts/deployment; distinguish manual microphone, synthetic audio and text-only checks. Procedure: [Day 1 human acceptance runbook](day1-acceptance-runbook.md); record: [pending evidence](day1-acceptance-evidence.md).

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D1.8 — Reconcile obsolete deployment notes and mark baseline release only after acceptance

- [ ] Accomplished

**Depends on:** D1.1–D1.7.

**What to do:** Implement or verify this scope: Reconcile obsolete deployment notes and mark baseline release only after acceptance. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Current docs match observed deployments.
- every D1 acceptance gate has evidence before release marking.

**Verification:** Review D1.1–D1.7 records and git revision; do not create a baseline release marker while any gate remains pending. Procedure: [Day 1 human acceptance runbook](day1-acceptance-runbook.md); record: [pending evidence](day1-acceptance-evidence.md).

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.


## Day 2 — memory and decision routing

Execute in order; keep mandatory gates ahead of optional work.

### D2.1 — Define immutable decision, action, settings, context and model-request contracts

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Define immutable decision, action, settings, context and model-request contracts. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Contracts reject unknown actions/invalid tiers.
- domain transforms cannot mutate their input.
- generated client types agree.

**Verification:** Add pure contract/property tests and regenerate API contracts; run pytest and frontend check.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.2 — Add additive migrations: pgvector, atomic memory/provenance, settings, revisions, proposals, operations, snapshots and metrics

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Add additive migrations: pgvector, atomic memory/provenance, settings, revisions, proposals, operations, snapshots and metrics. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Clean and existing PostgreSQL databases migrate successfully.
- vector dimension is 1536.
- existing turns/budget totals survive.

**Verification:** Apply migrations to disposable PostgreSQL fixtures, inspect schema and compare seeded records before/after; do not reset hosted data.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.3 — Extend tenant restrictions and shared budget accounting to every new path

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Extend tenant restrictions and shared budget accounting to every new path. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- User A cannot access B's new resources.
- denied budget produces no external request.
- new tables retain private grants.

**Verification:** Create two-owner fixtures; exercise every new read/write endpoint and fake external boundaries; inspect grants.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.4 — Implement explicit memory persistence, embeddings, correction versions and cross-conversation recall

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement explicit memory persistence, embeddings, correction versions and cross-conversation recall. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Explicit fact survives a new conversation and process connection.
- retrieval includes source/version.
- correction does not return contradictory active versions.

**Verification:** Seed city and budget separately, restart test service, retrieve in another conversation, correct city and inspect provenance/results.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.5 — Compile permitted history/memory/tool results under one revision

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Compile permitted history/memory/tool results under one revision. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Manifest/messages use one revision.
- excluded IDs and tracked derivations are absent.
- repeat inputs have identical canonical hashes.

**Verification:** Add compiler properties and worker integration with stale in-memory history; inspect canonical messages and included IDs.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.6 — Implement shared UI/voice action dispatcher and exact local-command parser

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement shared UI/voice action dispatcher and exact local-command parser. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Equivalent UI/voice actions reach the same handler.
- unknown actions, quoted/negated commands and unsupported compounds do not mutate state.

**Verification:** Use table-driven parser fixtures and handler spies; compare resulting state and action IDs for equivalent submissions.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.7 — Implement semantic search, keep-exceptions, preview, confirmation, atomic exclusion and revision-safe Undo by text

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement semantic search, keep-exceptions, preview, confirmation, atomic exclusion and revision-safe Undo by text. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Preview alone changes nothing.
- confirmation excludes only authorized IDs while preserving budget.
- stale/duplicate confirmation is safe.
- Undo restores exact operation.

**Verification:** Seed atomic trip dates/hotel/budget, run full text workflow and assert rows/revisions/diffs after every step.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.8 — Ensure weather cannot bypass excluded location context

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Ensure weather cannot bypass excluded location context. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Included city is usable.
- excluded city/source is not.
- explicit city still works.
- restore makes memory eligible again.

**Verification:** Use a fixture with no alternate location and a recording fake weather boundary; assert when called and its city argument.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.9 — Implement configurable five-tier response policy and update current 800-token bound safely

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement configurable five-tier response policy and update current 800-token bound safely. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Caps are exactly configurable 64/128/256/512/1024.
- preferences/explicit instructions follow policy.
- complexity never changes reasoning.

**Verification:** Add tier/property tests and inspect outgoing adapter requests, including tier 5 and exhausted speech allowance.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.10 — Introduce provider-independent streaming request/response adapters

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Introduce provider-independent streaming request/response adapters. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Both adapters preserve instructions/history/memory/revision/tools/tier and normalize text/tool/usage/errors without hidden retries.

**Verification:** Feed the same immutable request into fake provider transports and compare semantic payloads and normalized event sequences.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.11 — Explicitly disable DeepSeek thinking; activate primary only after user confirms funding

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Explicitly disable DeepSeek thinking; activate primary only after user confirms funding. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- DeepSeek request explicitly disables thinking and streams.
- unfunded default remains temporary OpenAI.
- funded activation has guarded success evidence.

**Verification:** Inspect fake transport payload then, only after funding confirmation, perform one bounded access check and verify configured primary.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.12 — Implement eligible pre-output OpenAI fallback, deadlines, budget denial and partial-output handling

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement eligible pre-output OpenAI fallback, deadlines, budget denial and partial-output handling. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Eligible pre-output failure causes one reserved fallback.
- emitted text/tool calls, cancellation/config errors and quota denial cause none.

**Verification:** Inject deterministic failure streams and budget denials; assert call counts, reservation-before-send order and terminal partial status.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.13 — Use stable tool operation identities; preserve completed and uncertain outcomes

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Use stable tool operation identities; preserve completed and uncertain outcomes. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Repeated operation identity returns stored result.
- completed/unknown effects never execute again.
- different operations remain distinct.

**Verification:** Replay a stable operation twice with a counting fake tool, simulate unknown delivery and assert execution count and ledger state.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.14 — Configure Jev key (pending), pinned model, no retries, deadline, ledger and bypass

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Configure Jev key (pending), pinned model, no retries, deadline, ledger and bypass. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Pinned Jev uses no retries, 750ms deadline and reservations.
- missing key/timeout/credit failure bypass.
- three failures open five-minute bypass.

**Verification:** Use fake clock/transport tests; only after key setup perform one guarded access check without logging key or request content.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.15 — Implement separate intent/tier/complexity classifications and structured argument extraction

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement separate intent/tier/complexity classifications and structured argument extraction. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Intent/tier/complexity remain separate validated choices.
- low-confidence/invalid/conflicting decisions cannot authorize mutation.
- extraction arguments validate.

**Verification:** Use all eight intents plus malformed probabilities and compound/negated examples; assert handler calls and clarification outcomes.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.16 — Benchmark 24 labeled cases against direct primary; enable only after documented gate

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Benchmark 24 labeled cases against direct primary; enable only after documented gate. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- 24 labeled cases have separate tuning/holdout.
- report raw agreement/latency/cost.
- automatic routing stays off unless >=90% agreement, no unsafe effects and <=250ms median overhead.

**Verification:** Run bounded text benchmark after funding/key gates, save labeled outputs and measurements; never claim holdout examples were unseen if tuned on them.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D2.17 — Run cross-session recall → exclude except budget → inspect next request → restore acceptance

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Run cross-session recall → exclude except budget → inspect next request → restore acceptance. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- New conversation recalls fact.
- exclude-trip-except-budget changes next request.
- Undo restores visibility with correct revision.

**Verification:** Run integrated text scenario on persisted memory; inspect exact IDs, proposal, revision and next compiled request at each step.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.


## Day 3 — interface and spoken context

Execute in order; keep mandatory gates ahead of optional work.

### D3.1 — Add Tailwind and planned three-column layout without losing baseline behavior

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Add Tailwind and planned three-column layout without losing baseline behavior. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Three-column layout renders desktop and collapses accessibly on narrow screens.
- existing history/voice controls still function.

**Verification:** Run frontend check/build/browser suite and inspect desktop/mobile screenshots for controls, overflow and reading order.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.2 — Implement accessible orb startup and idle/initializing/listening/processing/speaking/error states

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement accessible orb startup and idle/initializing/listening/processing/speaking/error states. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Orb starts one session, exposes all seven states, is keyboard-operable and respects reduced motion.
- repeated clicks do not duplicate dispatch.

**Verification:** Drive state fixtures and keyboard interactions in Playwright; count session requests and test denied microphone/retry.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.3 — Replace product call wording with voice-chat terminology and distinct admission errors

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Replace product call wording with voice-chat terminology and distinct admission errors. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Product labels use voice chat.
- existing-session, capacity and usage failures show different actionable messages.
- SDK/tool-call terms remain valid.

**Verification:** Search user-visible strings and trigger each admission failure fixture; assert message and recovery action, not raw CALL_LIMIT_REACHED.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.4 — Build approved user settings; keep provider/model/paid controls internal

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Build approved user settings; keep provider/model/paid controls internal. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Only approved settings appear.
- reload retains preferences.
- invalid values/voices are rejected server-side.
- no provider or key controls appear.

**Verification:** Exercise settings with UI and direct API invalid payloads, reload, inspect rendered controls and bundle for secrets.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.5 — Wire voice commands to the same settings/actions and synchronize UI controls

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Wire voice commands to the same settings/actions and synchronize UI controls. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Speak-slower/hide-captions/approved-voice commands update identical state and controls as UI actions without conversational LLM generation for exact commands.

**Verification:** Compare voice transcript and GUI fixtures, handler traces, persisted settings and generation call counts.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.6 — Verify approved voice/speed changes at synthesis boundaries

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify approved voice/speed changes at synthesis boundaries. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Playing audio retains old settings.
- next synthesis uses new approved voice/speed.
- pending becomes applied at boundary.

**Verification:** Use a fake TTS stream holding current speech open, change settings and inspect next synthesis arguments; perform one short guarded audio check.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.7 — Implement Simple/Developer toggle without reconnecting voice

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Implement Simple/Developer toggle without reconnecting voice. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Toggle preserves room identity, conversation, settings and pending proposal.
- advanced controls are authorized beyond CSS visibility.

**Verification:** Toggle repeatedly in browser with runtime spies and pending proposal; assert no extra connection or dispatch.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.8 — Build semantic memory explorer with provenance and stored/retrieved/included/excluded distinctions

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Build semantic memory explorer with provenance and stored/retrieved/included/excluded distinctions. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Owner-scoped semantic results show provenance and distinct stored/retrieved/included/excluded status, including empty/error states.

**Verification:** Seed memories with known states for two owners, search and compare UI labels with backend results and snapshot IDs.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.9 — Wire spoken context proposals, confirmation, diffs and restoration

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Wire spoken context proposals, confirmation, diffs and restoration. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Final spoken command creates preview, not mutation.
- confirmation commits exact IDs.
- diff/restore match text workflow.
- ambiguity asks clarification.

**Verification:** Feed finalized transcript fixtures then one short guarded spoken workflow; inspect proposals, handler IDs and revision transitions.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.10 — Capture actual serialized outgoing model requests separately from canonical snapshots

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Capture actual serialized outgoing model requests separately from canonical snapshots. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Displayed capture matches serialized inference body excluding credentials.
- canonical representation is separately labeled.
- continuations/fallback attempts stay distinct.

**Verification:** Intercept fake transport payload and compare snapshot JSON; exercise missing capture and assert honest unavailable label.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.11 — Show essential routing, usage, tool and audio metrics; null means unmeasured

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Show essential routing, usage, tool and audio metrics; null means unmeasured. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Known timing/usage events display correct units and attempt IDs.
- unmeasured values remain null/unavailable rather than zero.

**Verification:** Inject fixed timestamps and usage fixtures; verify calculated durations and UI labels against expected arithmetic.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.12 — Qualify Arabic MSA/Libyan/mixed speech and chosen TTS voice; retain experimental status if inadequate

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Qualify Arabic MSA/Libyan/mixed speech and chosen TTS voice; retain experimental status if inadequate. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- MSA, Libyan and mixed samples have references, model/voice IDs and omission/intelligibility notes.
- unqualified combinations remain experimental.

**Verification:** Run bounded approved samples and human listening review; save raw observations, avoid invented accuracy targets or automatic recording replay.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.13 — Verify reviewer access, production deployment, setup docs and presentation flow

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify reviewer access, production deployment, setup docs and presentation flow. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Fresh reviewer can reach HTTPS app using documented access and complete mandatory flow.
- anonymous paid inference stays blocked.

**Verification:** Rehearse with fresh browser identity; verify deployment revisions, secure access and README instructions without copying secrets into docs.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D3.14 — Freeze features and record final inventory

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Freeze features and record final inventory. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Inventory names shipped/experimental/deferred features and final tested revisions.
- every mandatory gap is explicit.

**Verification:** Audit checklist and release evidence, record freeze revision; no new feature work after freeze.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.


## Day 4 — revision only

Execute in order; keep mandatory gates ahead of optional work.

### D4.1 — Run regression/property/browser suites and real PostgreSQL integration checks

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Run regression/property/browser suites and real PostgreSQL integration checks. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Backend/property/frontend/browser and actual PostgreSQL tests pass without silently skipped required checks.

**Verification:** Run uv run pytest -q plus pnpm --dir apps/web check, build and test:browser; run new PostgreSQL tests against disposable DB and retain results.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D4.2 — Check Chrome, Firefox and mobile layout/accessibility

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Check Chrome, Firefox and mobile layout/accessibility. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Mandatory flow works in Chrome/Firefox and narrow viewport.
- keyboard focus, captions and microphone recovery remain accessible.

**Verification:** Run browser matrix manually or through configured projects; record browser versions, viewport and exact failures.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D4.3 — Exercise Jev failure, primary/fallback failure, interruption, reconnect and exhausted budgets

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Exercise Jev failure, primary/fallback failure, interruption, reconnect and exhausted budgets. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Failure matrix terminates safely without provider loops, stale playback, budget bypass or duplicate tool effects.

**Verification:** Run deterministic boundary failures first, then minimal hosted lifecycle checks within allowances; inspect traces/counters.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D4.4 — Verify isolation, revision conflicts, duplicate submissions, Undo and request capture

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Verify isolation, revision conflicts, duplicate submissions, Undo and request capture. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Wrong owner, stale revision and duplicate writes fail safely.
- Undo and captured requests match committed revision.

**Verification:** Run two-owner/concurrent-operation fixtures and compare database changes with serialized request evidence.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D4.5 — Check hosted service health, reviewer access and API balances

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Check hosted service health, reviewer access and API balances. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Frontend authentication, API health, worker availability and DB access pass.
- reviewer credentials work.
- remaining balances cover rehearsal.

**Verification:** Use read-only service checks and a fresh authenticated browser; record timestamps without secrets or credit purchases.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D4.6 — Record actual observed latency/usage; distinguish estimates and reservations

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Record actual observed latency/usage; distinguish estimates and reservations. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Report distinguishes raw latency samples, actual vendor usage, estimates and conservative reservations.
- no invented percentiles for tiny samples.

**Verification:** Collect correlated turn timestamps/usage and ledger before/after; verify units and state sample size explicitly.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D4.7 — Fix defects and reconcile documentation without new features

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Fix defects and reconcile documentation without new features. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Every fix addresses a reproduced defect with targeted verification.
- docs reflect final behavior.
- no deferred feature is introduced.

**Verification:** Review diffs against defect list, rerun affected tests and inspect final docs/release revision.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### D4.8 — Rehearse presentation and produce clearly labeled backup clips

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Rehearse presentation and produce clearly labeled backup clips. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- A 3–5 minute rehearsal completes mandatory walkthrough.
- backup clips clearly say recorded and expose no credentials.

**Verification:** Time rehearsal, inspect clips and compare shown features with release inventory; keep local media ignored.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.


## Verification requirements

### V1 — Hypothesis: determinism, exclusion idempotence, protected records, tenant isolation, revisions, Undo, tier bounds, dispatch and concurrent spending

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Hypothesis: determinism, exclusion idempotence, protected records, tenant isolation, revisions, Undo, tier bounds, dispatch and concurrent spending. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Generated properties assert determinism, exclusions, protection, isolation, revision/Undo laws, tiers and concurrent budgets without paid models.

**Verification:** Add missing properties and run uv run pytest -q; retain failing seeds and resolved counterexamples.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### V2 — Jev: valid/unknown/malformed outputs, low confidence, conflicting intents, timeout, missing key, depleted credit and graceful bypass

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Jev: valid/unknown/malformed outputs, low confidence, conflicting intents, timeout, missing key, depleted credit and graceful bypass. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- All listed Jev invalid/uncertain/unavailable cases produce defined safe outcomes and no unauthorized actions.

**Verification:** Inject valid/unknown/malformed/low-confidence/conflicting/timeout/key/credit responses; assert decisions and effect counts.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### V3 — Commands: negation/quotation/compound safety, authorized arguments and UI/voice state equivalence

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Commands: negation/quotation/compound safety, authorized arguments and UI/voice state equivalence. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Negations, quotations and unsupported compounds never execute.
- valid equivalent UI/voice commands produce equal state.

**Verification:** Run table-driven parser/dispatcher tests with ownership-invalid arguments and compare effects.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### V4 — Failover: eligible early failure, no retry after text/tool emission, no duplicated effects, no fallback on cancellation/configuration/quota errors

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Failover: eligible early failure, no retry after text/tool emission, no duplicated effects, no fallback on cancellation/configuration/quota errors. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Only eligible pre-output failures fall back.
- partial output and tool effects never replay.
- cancellation/configuration/quota deny fallback.

**Verification:** Run primary/fallback stream fixtures and count provider/tool/budget calls and terminal states.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### V5 — Integration: real vector retrieval, scoped proposals, source dependencies, worker fresh context, actual payload capture and stable tool outcomes

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Integration: real vector retrieval, scoped proposals, source dependencies, worker fresh context, actual payload capture and stable tool outcomes. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Real vector retrieval and scoped proposals honor ownership/exclusions.
- fresh worker input, payload capture and stable tool identity match durable state.

**Verification:** Run disposable PostgreSQL plus fake transport integration and inspect IDs, revisions, payloads and operation rows.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### V6 — Paid checks remain small and ledger-guarded; no automatic replay of user recordings

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Paid checks remain small and ledger-guarded; no automatic replay of user recordings. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Every paid request reserves first.
- denied reservation sends nothing.
- recordings are not replayed automatically.

**Verification:** Inspect adapters/scripts and denial tests; record budget deltas for explicit live checks and keep artifacts ignored.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.


## Presentation acceptance

### P1 — Orb voice chat → atomic memories → new conversation recall/weather → spoken speed change → context explorer → exclude trip except budget → confirm/diff/request inspection → restore

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Orb voice chat → atomic memories → new conversation recall/weather → spoken speed change → context explorer → exclude trip except budget → confirm/diff/request inspection → restore. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Walkthrough demonstrates live orb speech, persistent recall/weather, settings, provenance, exclusion exception, diff/request inspection and restoration.

**Verification:** Use fresh demo identity and separate atomic city/trip/budget facts; follow architecture presentation sequence and record each observed outcome.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.

### P2 — Show controlled failover evidence, pure transformations, property tests, limitations and budget evidence

- [ ] Accomplished

**Depends on:** See prerequisite map below; independent checks may run once their feature exists.

**What to do:** Implement or verify this scope: Show controlled failover evidence, pure transformations, property tests, limitations and budget evidence. Use the architecture specification for policy; do not broaden the task into optional features.

**Acceptance criteria:**
- Presentation evidence distinguishes controlled failover, pure invariants, model limitations and actual usage from reservations.

**Verification:** Open test/code artifacts and recorded failover trace; verify narrative against released behavior without claiming nondeterministic guarantees.

**Completion evidence:** Pending. Record command/procedure, observed result, code/deployment revision and non-secret evidence path. Leave unchecked until every criterion passes.


## Deferred

Wake words, browser-local speech, extra providers, speculative partial routing, advanced statistics and implicit extraction are stretch. Permanent deletion is deferred. Day 4 adds no features.

## Prerequisite map and completion rules

D1 starts from M0; D2 starts after baseline acceptance. D2.2 follows D2.1; D2.3–D2.5 follow schema/contracts; D2.6 follows contracts; D2.7 follows retrieval/compiler/dispatcher; D2.8 follows D2.5 and D2.7. D2.9–D2.10 follow contracts; D2.11 follows adapters and funding confirmation; D2.12 follows adapters/budgets; D2.13 follows operation schema; D2.14 requires key setup; D2.15 requires dispatcher and Jev adapter; D2.16 requires classifications and funded primary; D2.17 requires D2.4–D2.13. D3 consumes the corresponding D2 capabilities; D3.5–D3.6 follow settings/dispatcher; D3.9 follows context operations/explorer; D3.13–D3.14 follow mandatory feature acceptance. D4 follows feature freeze. V1–V6 verify their corresponding implemented subsystems; P1/P2 require frozen deployed features.

Checked items retain their original implementation/verification scope. A new broader requirement gets a pending follow-up. Missing account access or human verification is pending, never a pass. Evidence distinguishes local implementation, local tests, deployment and hosted acceptance. Secrets and media stay ignored; all paid checks use the shared ledger.
