# Sarjy — Voice Context Studio

Bilingual SvelteKit UI, FastAPI state service, and LiveKit Python speech worker.

## Local development

Install uv, Node 22, and pnpm 10.32.1. From the repository root:

```sh
uv sync --locked
pnpm --dir apps/web install --frozen-lockfile
uv run uvicorn app.main:app --app-dir apps/api --reload
```

Fill the existing root `.env` with the names in `.env.example` (do not overwrite existing secrets). SQLite defaults to `sarjy.db` and bootstraps locally. In another terminal:

```sh
pnpm --dir apps/web dev
```

The frontend proxies `/api/v1/*` to `API_INTERNAL_URL`, defaulting to `http://localhost:8000`. Set this environment variable in the frontend process if using another address. Browser requests use an isolated HttpOnly demo cookie. Conversation history works without speech credentials.

For real speech, configure `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `AGENT_SERVICE_SECRET`, `DEEPGRAM_API_KEY`, `DEEPSEEK_API_KEY`, and `ELEVEN_API_KEY`. Use the same service secret in the API and worker. Set `API_INTERNAL_URL` in the worker to the reachable API address. Then run:

```sh
uv run -m livekit.agents download-files
uv run services/voice-agent/main.py dev
```

The temporary default is Nova-3 English → OpenAI GPT-6 Luna → ElevenLabs Flash v2.5. DeepSeek Flash direct remains available for an explicit switch after funding. No fallback is silently selected. Arabic voice quality and live provider entitlement still require manual validation.

## Checks

```sh
uv run pytest -q
pnpm --dir apps/web check
pnpm --dir apps/web build
pnpm --dir apps/web exec playwright install chromium
pnpm --dir apps/web test:browser
```

Pure tests use immutable records and Hypothesis invariants. Database/API tests cover real persistence and authorization.

Optional PostgreSQL migration parity against a disposable database (never a hosted one):

```sh
TEST_POSTGRES_URL='postgresql+asyncpg://postgres:test@127.0.0.1:55432/sarjy' uv run pytest -q tests/test_migrations.py
```

## Deployment

Build images from repository root with `infra/api.Dockerfile`, `infra/web.Dockerfile`, and `infra/agent.Dockerfile`. Use CranL for frontend/API, LiveKit Build for the worker, and Supabase PostgreSQL. Set `DATABASE_URL` to an asyncpg SQLAlchemy URL, run `uv run alembic upgrade head` before API startup, and set `COOKIE_SECURE=true` in production. Never expose provider secrets to the frontend. Configure SvelteKit's trusted public origin for the deployed proxy; retain CSRF checks.

LiveKit Build quotas must be checked in the account before testing. Hosted agent minutes and direct provider charges are separate. Calls are capped at ten minutes, one active per identity and two globally. Each identity can reserve three calls; the application can reserve 80 calls per calendar month (at most 800 agent minutes), including failed dispatch reservations. This conservative cap does not account for other LiveKit projects; check the account dashboard. Agent cold starts may take 10–20 seconds. Preserve 200 agent minutes for reviewers. The worker stops after 60 seconds without a browser participant. Raw agent recording is disabled.

**Public URL:** not deployed yet; hosting credentials and provider setup remain required.

## Current limits

- Real provider replies and prerecorded English STT are verified; live English conversation acceptance remains pending.
- Day 1 snapshots capture initial context assembly; tool continuations are retained in the worker but do not yet receive separate request snapshots.
- Persistent history is implemented; extracted memory and context exclusion/Undo are Day 2 work.
- Managed DeepSeek/Cartesia fallback candidates are not enabled.
- Disposable PostgreSQL migration parity passed; Supabase account connectivity remains unverified.
- Public identity creation needs further abuse controls before unrestricted public exposure.
- Open-Meteo weather is called server-side, with clarification for ambiguous cities. Weather data: https://open-meteo.com/ . Use is subject to its attribution and service terms.

## Day 1 acceptance status

Verified locally: anonymous identity isolation, conversation persistence, worker authorization/idempotent callbacks, deterministic bounded prompt compilation, initial migrations, a live Open-Meteo request for Tripoli, bilingual browser controls, same-origin proxy CSRF rejection, and explicit missing-configuration errors.

Pending real-account checks: MSA/dialect/code-switching recognition; selected ElevenLabs Arabic voice intelligibility; spoken/typed shared turn flow; interruption and reconnect with real audio; measured free-tier usage; PostgreSQL integration; and the public CranL URL.

The interface language selected at call start selects the speech pipeline language. Changing labels during a call does not rebuild the audio pipeline. Ask explicitly to change the reply language during that call.

## Shared contracts

Regenerate the checked-in OpenAPI schema and TypeScript input contracts after changing API models:

```sh
uv run python scripts/export_contracts.py
pnpm dlx openapi-typescript@7.13.0 packages/contracts/openapi.json -o packages/contracts/api.d.ts
```

SQLite and disposable PostgreSQL migration/model-parity checks passed locally. All three Docker images build; the frontend HTTP server, API identity/database startup, and worker CLI were smoke-tested. These checks do not establish live speech quality or deployed account access.

## Voice selection

Voice is a user setting, not an environment variable. Use the voice picker to search the configured ElevenLabs account's available voices; the API key remains server-side. The picker excludes custom-rate voices. Language labels are provider metadata, not proof of Arabic playback quality.

The chosen voice is remembered in this browser and sent explicitly when a call starts. The backend revalidates it and stores it in the session pipeline/snapshots. Changes apply to the next call; an unavailable voice causes an explicit error, never a silent substitution. `ELEVEN_VOICE_ID` is no longer read; an old entry in a local `.env` may be removed.

Conversational personalization is planned next: “I want to speak to a British young lady” becomes model-interpreted catalog criteria, followed by deterministic selection of an accessible voice and an honest no-match path. Spoken in-call changes and response-boundary voice switching are not implemented in the initial picker.

## Development spend limits

OpenAI is the temporary default (`openai_direct`, `gpt-6-luna`, Responses API, reasoning disabled). DeepSeek remains supported; switch `DEFAULT_LLM_PROVIDER=deepseek_direct` only after the user confirms a top-up. Set `OPENAI_API_KEY` server-side.

OpenAI has a separate **$1/day development ceiling**, enforced with durable conservative reservations of **$0.02 before each LLM request** (50 requests/day, including tool continuations). This deliberately stops earlier than token billing would require. The bounded text-only request uses at most 64KB canonical context, the one fixed weather tool, 800 output tokens (96 by default), Standard tier, no provider built-in tools, and no SDK/LiveKit retries. The allowance is based on the checked 11 November 2026 Luna rates ($0.10/M input, $0.50/M output); recheck pricing before changing models or request bounds. Reservations are not invoices and are not refunded after errors, interruptions, or uncertain delivery.

ElevenLabs is more conservative: **500 characters/day**, reserved before text is forwarded for synthesis. No unattended audio simulations, repeated synthesis benchmarks, paid top-ups, or quota-triggered provider switching. Prefer text-only development. Partial/failed synthesis reservations also remain charged to this local allowance.

Both limits reset at midnight **Africa/Tripoli**, persist across restarts, are shared through the backend database, and fail closed if the budget service fails. These guards cover Sarjy's guarded application/development calls, not unrelated clients using the same keys or OpenAI subscriptions. Never delete/reset the budget database to regain allowance. Any additional paid development script must reserve against the same ledger before requesting inference.

Apply `uv run alembic upgrade head` before running the new version against a migrated database. SQLite local bootstrap also creates the budget table.

For a minimal OpenAI-only access check after migrations, run `uv run python scripts/check_openai.py`. It makes exactly one small request after reserving $0.02 against the same database ledger; it never synthesizes audio or retries automatically.

## Protected development deployment

Set `DEVELOPMENT_PROTECTED=true` on both web and API. Web requires `DEVELOPMENT_USER`, a strong `DEVELOPMENT_PASSWORD`, and `WEB_SERVICE_SECRET`; API requires the same `WEB_SERVICE_SECRET`. The web server challenges every request with HTTP Basic authentication; the proxy injects the independent backend secret. Worker routes retain worker authentication. Missing required protection secrets fail startup. Deploy only over HTTPS. Keep these values in hosting secrets. Arabic speech validation is deferred to Day 3.

Hosting is not configured yet. Connect CranL with `codex mcp add cranl --url https://app.cranl.com/api/mcp` and `codex mcp login cranl`. Supabase setup and LiveKit eu-central deployment remain pending. Preserve the existing daily budget ledger before switching databases; do not run two independent spending ledgers.

## Hosted development attempt (11 November 2026)

CranL apps `sarjy-api` and `sarjy-web` were created in Netherlands with protection secrets configured before deployment. Deployment is **not working**: API source deployment returned platform internal errors through inline and signed uploads; the web build reports a missing source directory on CranL's build server and its URL returns 502. Details are in ignored `artifacts/deployment/status.json`; access credentials are in ignored `artifacts/deployment/access.json` (mode 0600). Never publish these artifacts. LiveKit worker deployment and hosted acceptance remain pending. No inference was used during this deployment attempt.

## GitHub release workflow

The repository must remain private. Review ignore rules and the complete staged diff before any commit; never publish local databases, provider credentials or artifacts. Develop on `main`; promote only commits whose `checks` workflow passed to `deploy/api`, verify the API, then promote that same commit to `deploy/web`. CranL builds from repository root using `infra/api.Dockerfile` (port 8000) and `infra/web.Dockerfile` (port 3000). Keep PR previews disabled. Configure protection and all server-side secrets before first boot. Preserve Supabase and reconcile the budget ledger before enabling hosted inference. Record the last successful SHA for recovery; database rollback is never automatic.

## Hosted Day 1 verification

Frontend: https://sarjy-web-dev-otbaod.cranl.net (password required). API: https://sarjy-api-dev-hiqked.cranl.net. Anonymous page/static access and direct API requests are blocked. Authenticated frontend conversation creation and Supabase persistence were verified through Chromium; the local HTTP client's TLS stack was reset by the edge. Credentials remain only in ignored deployment storage.

Deployment branches add the appropriate root `Dockerfile` to the same tested source revision; CranL's nested Dockerfile build did not include repository-root files. The API and frontend root-Dockerfile branch checks passed.

Prepare the worker context with `python scripts/prepare_worker_deployment.py`. It includes only lockfiles, manifests, worker Python source and the worker Dockerfile. Inject secrets separately through LiveKit. Worker deployment and live English voice acceptance remain required before declaring Day 1 complete. Arabic speech stays experimental until Day 3.
