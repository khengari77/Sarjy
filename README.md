# Sarjy — Voice Context Studio

A bilingual voice assistant that remembers across conversations and lets you see, shape and undo the context a model receives.

## What it is

Sarjy is a working voice and text assistant built around **controllable context**. You talk to it in the browser, it remembers durable facts and preferences, and it compiles a bounded prompt from that memory for every request. A separate deep dive makes that compilation inspectable: you can preview what will be included, exclude a subset while keeping an exception, review the diff, and Undo.

```mermaid
flowchart LR
    B["Browser<br/>SvelteKit UI"] --> W["CranL web<br/>adapter-node"]
    W --> A["FastAPI API<br/>identity, memory, context, actions"]
    A --> D[("PostgreSQL<br/>Supabase; SQLite locally")]
    B <-->|"WebRTC audio"| L["LiveKit Cloud"]
    L --> K["Python voice worker"]
    K --> P["STT / LLM / TTS providers"]
    A --> M["Open-Meteo"]
    K --> A
```

## Live demo

**https://sarjy-web-dev-otbaod.cranl.net**

The development deployment is protected. Reviewers open an invitation link or enter the shared reviewer token supplied by the operator; neither is published in this repository. Access is enforced server-side and expires. See [reviewer access](docs/reviewer-access.md).

## Quickstart

Install `uv`, Node 22 and pnpm 10.32.1. From a fresh checkout:

```sh
uv sync --locked
pnpm --dir apps/web install --frozen-lockfile
```

Create a local `.env` from `.env.example` and fill only what you need; never commit it. SQLite (`DATABASE_URL=sqlite+aiosqlite:///./sarjy.db`) bootstraps locally, so conversation history works without speech credentials.

Run the API and web app in separate terminals:

```sh
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --app-dir apps/api --reload
pnpm --dir apps/web dev
```

The frontend proxies `/api/v1/*` to `API_INTERNAL_URL`, defaulting to `http://localhost:8000`. Browser requests use an isolated HttpOnly demo cookie.

For real speech, set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `AGENT_SERVICE_SECRET`, `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, and optionally `ELEVEN_API_KEY` (when `TTS_PROVIDER=elevenlabs`). Use the same `AGENT_SERVICE_SECRET` and a reachable `API_INTERNAL_URL` in the API and worker. Then:

```sh
uv run -m livekit.agents download-files
PYTHONPATH=apps/api uv run services/voice-agent/main.py dev
```

The speech pipeline uses Deepgram Nova-3 English STT and Deepgram Aura-2 TTS by default; `TTS_PROVIDER=elevenlabs` selects ElevenLabs Flash v2.5. The LLM provider comes from `DEFAULT_LLM_PROVIDER`. Missing configuration fails explicitly; no provider is silently substituted.

## The deep dive: controllable context

The experiment is not a bigger prompt window. It is a small, auditable pipeline in which **the model proposes and deterministic code decides** which memories and actions are allowed.

The representative request is a keep-exception:

> Exclude my travel memories, but keep my budget.

The flow is deliberately staged:

1. **Preview** — resolve the request against stored memories and show the candidate matches, the records that must be preserved, and any ambiguous records that need a clearer decision.
2. **Confirm** — apply the exclusion only after an explicit decision. A unique voice "forget" hides one memory in one step and says how to undo it; compound keep-exceptions and exact-id selections go through the preview/confirm API used by the memory explorer.
3. **Diff** — show exactly what is removed, what is preserved, and what stays excluded.
4. **Next request** — the context compiler uses the reduced, still-bounded selection; the model sees the exception-compiled result, not the raw memory store.
5. **Undo** — restore the excluded records and show the restored diff.

Why this shape: a language model is good at interpreting intent but is not a trustworthy authority over storage, consent or spend. Matching, exclusion lineage, budget reservation and action dispatch are deterministic, typed and tested; the model proposes an interpretation and the application either accepts it or reports honestly that nothing changed. The same boundary covers weather calls, UI commands, Wordle actions and memory writes.

## External API

Sarjy uses [Open-Meteo](https://open-meteo.com/) for server-side weather lookups, including geocoding and clarification when a city is ambiguous. Weather turns a voice request into a useful, verifiable real-world action and gives cross-session memory a concrete test: a remembered home city is reused in a later session. Open-Meteo's low-friction API keeps the project focused on the context deep dive while still demonstrating reliable tool calling; use is subject to its attribution and service terms.

## Architecture

Three deployables share one repository:

- `apps/web` — SvelteKit 5 + TypeScript UI, adapter-node, same-origin proxy, browser audio and the memory/Wordle panels.
- `apps/api` — FastAPI service owning identity, conversations, memory, context compilation, budgets, actions and persistence.
- `services/voice-agent` — long-running LiveKit Agents worker that joins rooms, streams STT → LLM → TTS, and calls the API for authoritative context and callbacks.

Design boundaries:

- **Pure context compiler.** Domain selection and prompt assembly are pure functions over immutable records; I/O lives in services and adapters.
- **Consent-first memory.** Facts are captured explicitly, versioned, owner-scoped and never shared across identities. Hides are reversible.
- **Typed action dispatcher.** Voice commands become typed actions with idempotency and leases, not free-form database access.
- **Durable budgets.** Spend and call reservations live in the database, survive restarts and fail closed.
- **Provider adapters.** LLM, STT, TTS and external tools sit behind narrow interfaces, so providers can be swapped without leaking into the domain.

Deeper material: [the Version 5 architecture and plan](sarjy_voice_context_studio_plan_v4_arabic_speech.md), [execution checklist](docs/execution_checklist_v5.md), [typed actions](docs/typed-actions.md), [memory UI notes](apps/web/src/lib/memory/README.md).

## Checks

Run from a worktree with the locked dependencies installed:

```sh
uv run pytest -q
pnpm --dir apps/web check
pnpm --dir apps/web build
pnpm --dir apps/web exec vitest run
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
```

Current evidence for `origin/main` at `ed5c496` (19 November 2026):

- Python: **1,183 passed, 20 skipped, 0 failed** in **13:32** (`uv run pytest -q`).
- Vitest: **52 files, 443 tests passed** in **53.6s**.
- `svelte-check`: **0 errors, 1 warning**.
- `pnpm --dir apps/web build`: succeeds.
- Ruff, strict mypy and import-linter were green on the same source tree.

Database/API tests exercise real persistence and authorization; pure tests use immutable records and Hypothesis invariants. PostgreSQL parity is optional and requires a disposable database, never a hosted one:

```sh
TEST_POSTGRES_URL='postgresql+asyncpg://postgres:test@127.0.0.1:55432/sarjy' \
  uv run pytest -q tests/test_migrations.py
```

Browser and hosted checks that need LiveKit, provider keys or a real microphone are vendor-gated and are not part of the default CI claim. **Not verified here:** live human English microphone acceptance, measured conversation latency, and a hosted re-run after migration `0015_intro_seen`.

## Deployment and provenance

- `origin/main` is `ed5c496` (merge of PR #191, `integration/living-cloud`).
- Live sources are `deploy/web` at `905e65c` and `deploy/api` at `2c1da07`. Their code trees are identical to `main`; the only difference is the root `Dockerfile` each deploy branch adds for CranL (`git diff --shortstat origin/deploy/web origin/main` shows one file, and the code-only diff is empty).
- Because the code is identical, the live app includes the Wordle UI and voice commands, the intro greeting, generated conversation titles and the context blocks described above.
- The live database has migration `0014_reviewer_access`; `main` adds `0015_intro_seen`. Applying migrations is an operational action, not part of this documentation change.
- The previous README recorded release `9148a60` as live. That line is stale and has been removed; `9148a60` is not the current deployment.

Release process: merge only a reviewed, green commit into `main`; promote that same commit to `deploy/api`, verify it, then promote it to `deploy/web`. CranL builds from the repository root with `infra/api.Dockerfile` (port 8000) and `infra/web.Dockerfile` (port 3000). Never roll the database back automatically; keep the last successful SHA for recovery.

## Security, privacy and spend

- **Protected development.** `DEVELOPMENT_PROTECTED=true` is required on web and API. The web edge admits a reviewer only through the shared token or a per-person invitation link, then issues a signed, expiring cookie. The API re-authorises every `/v1/*` request; a revoked or expired invitation opens no data.
- **Isolation.** Identities, conversations and memories are owner-scoped. Supabase tables use row-level security and restrict public grants. Worker routes require the shared service secret.
- **No raw agent recording.** Audio is processed for the live session; raw agent recordings are disabled.
- **Application spend guards, not vendor quotas.** OpenAI is capped at **$1/day** with durable **$0.02** reservations before each LLM request. Deepgram TTS is capped at **50,000 characters/day**; ElevenLabs at **2,500 characters/day**; the Jev UI classifier at **2,000 requests/day**. Demo calls are limited to **3 per identity**, **80 per calendar month**, **10 minutes** each. Budget ledgers reset at midnight Africa/Tripoli, persist across restarts and fail closed.
- **No secrets in git.** Provider keys, reviewer tokens and deployment credentials live in hosting secrets or an ignored local `.env`; never commit them, local databases or generated artifacts.

## Known limits

- Real provider replies and prerecorded English STT are verified; **live human English microphone acceptance is still pending**.
- **Latency is not measured.** No latency number is claimed; agent cold starts can take 10–20 seconds.
- **Arabic is retained but hidden.** The localization and provider code still support Arabic, but the language switch is hidden and the English Deepgram voice has no Arabic. Arabic quality and entitlement are unqualified.
- Public identity creation needs stronger abuse controls before unrestricted exposure, and data-retention policy is still a gap.
- Wordle depends on an external publisher source; an outage is reported as unavailable rather than masked with a fabricated answer.

## Repository map

| Path | Contents |
| --- | --- |
| `apps/api/` | FastAPI app: domain, services, infrastructure, routes |
| `apps/web/` | SvelteKit UI, voice client, memory and Wordle panels |
| `services/voice-agent/` | LiveKit worker, provider factory, tools, snapshots |
| `packages/contracts/` | Checked-in OpenAPI schema and generated TypeScript types |
| `migrations/` | Alembic migrations `0001`–`0015` |
| `infra/` | API, web and agent Dockerfiles |
| `tests/` | Python unit, property, API and database tests |
| `scripts/` | Contracts, deployment, diagnostics and reviewer tooling |
| `docs/` | Operational notes, evidence and design records |

## Documentation

- [docs/](docs/) — operational notes and evidence
- [CONTRIBUTING.md](CONTRIBUTING.md) — worktree, issue, verification and review rules
- [STYLE.md](STYLE.md) — Python and frontend style contract
- [Version 5 architecture and plan](sarjy_voice_context_studio_plan_v4_arabic_speech.md)
- [Wordle backend](docs/wordle.md)
- [Reviewer access](docs/reviewer-access.md)

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [STYLE.md](STYLE.md) before editing. Claim an issue, work in a fresh worktree and branch from `origin/main`, keep changes inside the assigned scope, run the documented checks, and open a pull request. Authors do not merge their own PRs, push to `main`, enable auto-merge or deploy; every merge needs review of the exact final SHA. Keep secrets, recordings, local databases and generated artifacts out of git.
