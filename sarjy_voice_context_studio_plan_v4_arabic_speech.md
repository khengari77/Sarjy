> **Operational update:** Release `4a80697` is deployed to both CranL apps; Supabase is at `0004_voice_cleanup`. Historical reconciliation and hosted startup/idempotent end passed. Typed-input weather and real audio playback passed. Human microphone/interruption/reconnect/refresh acceptance remains pending. See [verification evidence](docs/day1-hosted-verification.md). Day 2 migrations remain undeployed.

> **Version 5 policy:** English is the Days 1–2 baseline; Arabic speech remains experimental until Day 3. Development stays password-protected. DeepSeek is the final primary provider, OpenAI the reliability fallback; the temporary OpenAI development default remains until funding confirmation. No public provider/model selector. “Forget” means reversible conversation-scoped exclusion; permanent deletion is deferred.
>
> **Progress:** CranL frontend/API and Supabase persistence are verified; the non-root LiveKit worker was deployed and registered. Hosted voice/weather and lifecycle acceptance remain pending. pgvector is not yet installed. Jev has approximately $5 credit reported by the user; key setup and live validation are pending. See the linked execution checklist. Existing application changes are outside this documentation revision.

# Sarjy — Voice Context Studio
## Detailed implementation, model-provider, Arabic speech, benchmarking, and deployment plan — v5 (decision routing and conversational context)

**Status:** Architecture and execution specification; see [execution checklist](docs/execution_checklist_v5.md) for evidence-based progress
**Date:** 11 November 2026  
**Delivery rule:** **Three days to a feature-complete deployed product; day four is strictly revision, testing, bug fixing, and demo rehearsal.**  
**One-line positioning:** **Talk to the assistant. Talk to its context. See exactly what changed.**

> **Read this first.** The assignment explicitly requires: (1) a functioning voice assistant, (2) memory of facts and preferences **across sessions**, (3) at least **one useful external API** with a short justification, and (4) a working **public deployment URL** that reviewers can open without special setup. It invites **one deep dive**, including a self-chosen topic. Our deep dive is **conversational context engineering**, not latency benchmarking or a full model marketplace. The API comparison features are supporting infrastructure, not another unfinished deep dive.
>
> The names **“GPT-6 Tera”** and **“GPT-6.1 Luna”** do not correspond to the current OpenAI API catalog. The actual listed models are **GPT-6.1 Terra** (`gpt-6.1-terra`) and **GPT-6 Luna** (`gpt-6-luna`). The plan also offers `gpt-6.1-sol` for optional higher-capability testing. The directly addressed DeepSeek V4.1 Flash API model is `deepseek-flash`; its **LiveKit Inference identifier differs** (`deepseek-ai/deepseek-v4.1-flash`). Verify account entitlement before starting. See the source links in §21.
>
> **Arabic speech decision (11 November 2026):** **Deepgram Nova-3 Multilingual** is the primary Arabic **STT** candidate. **ElevenLabs Flash v2.5** is the primary Arabic **TTS** candidate; a compatible Arabic voice must be validated with real Arabic test sentences. Deepgram **Aura-2** is not an Arabic TTS option, and the current **Flux TTS** official voice/language catalog lists no Arabic voice or `flux-*-ar` model identifier. Do not infer Arabic availability from a generic marketing-page language claim; treat Deepgram Arabic TTS as **unavailable/unverified** until an official voice ID and successful API test establish support. This is a capability-gated decision, not a ban on future Deepgram models.
>
> **Hosting correction:** The chosen hosting provider is **CranL** ([cranl.com](https://cranl.com/)), not “Gronm.” CranL supports Git/Docker deployments, HTTPS app URLs, and managed databases. Its Free plan includes **two combined applications or databases**, with deployment in the Netherlands only. We will use CranL for FastAPI and, if SvelteKit deployment is confirmed in the first-day smoke test, for the SvelteKit app too; Supabase remains the external pgvector database and LiveKit Cloud remains the voice runtime. Confirm actual resource quotas, sleep behavior, and current pricing in the account before depending on the free tier.

---

## 0. Executive decisions

### Product

Build one polished **ChatGPT-like voice/text application**, with two display modes over the **same conversation**:

- **Simple mode:** conversation list, typed chat, mic, interruptible audio replies, visible transcripts, session history, and persistent preferences across sessions. Simple mode must remain fully usable without ever seeing developer tools.
- **Developer mode:** everything above **plus** read-only execution diagnostics, context records, sources/provenance, semantic search, proposed context transformations, before/after diffs, immutable per-turn request snapshots, and compact pipeline measurements.

**Hero interaction:** The user speaks or types: *“Forget everything about my trip to Riyadh, but keep the budget.”* The application interprets it as a **context-control command**, runs semantic search, displays **which records match**, lets the user confirm, atomically marks chosen records **excluded for the appropriate scope**, assembles the next model request without them, and shows **Undo + a before/after diff**. The next response is verifiably based on the new context. Normal assistant turns and context-control commands must be unambiguously separated.

### Platform / product rules

1. **All speech and LLM services are adapters**, chosen through one typed `PipelineConfig`, not hard-coded inside the session business logic.
2. **Remote speech** runs as a LiveKit Agent pipeline; **local browser speech** is a separate execution path behind a common frontend `VoiceRuntime` interface. Do **not** pretend an in-browser model can simply be passed as a server-side Python `livekit.agents.STT` or `TTS` object.
3. The **model-visible prompt is compiled from authoritative, versioned state**. The frontend cannot independently decide what the model knows.
4. **AI chooses candidates; deterministic code authorizes and commits changes.** Similarity retrieval alone never permanently erases facts.
5. An external API demonstration must be more than inference/embeddings: use **Open-Meteo weather + geocoding**.
6. **Feature freeze after day three.** Provider breadth remains an internal registry; public controls never select providers. A supported/unsupported status is preferable to a fake toggle.
7. Day one ends with a **public working URL**, even if it is ugly. Deploy continuously.

### Must ship / should ship / stretch

| Priority | Feature | Acceptance criterion |
|---|---|---|
| P0 | Voice conversation | User can speak, hear a reply, interrupt, and reconnect |
| P0 | Cross-session memory | Fact stated in chat A appears when relevant in chat B |
| P0 | External API | Weather tool actually calls Open-Meteo and reports its output |
| P0 | No-setup public URL | Reviewer enters through an isolated demo identity/session |
| P0 | Context-command demo | Semantic search → preview → exclude → next prompt → undo |
| P1 | Developer mode | Provenance, compiled context, diff, provider and basic metrics |
| P1 | DeepSeek primary + OpenAI reliability fallback | At least two LLM paths tested against the same interface |
| P1 | Two remote speech configurations | First prove Deepgram Nova-3 Arabic STT + ElevenLabs Flash v2.5 Arabic TTS; compare an independently verified Arabic-capable fallback |
| P2 | Third remote STT/TTS mix | Use if already tested, not merely installed |
| P2 | Browser-local TTS | Working experimental mode or documented benchmark, not day-three release blocker |
| P2 | Browser-local STT | Stretch; test cold-start, accuracy and streamed user experience |
| OUT | SIP, multi-agent experiments, full computer use, production compliance certification | Not part of three-day MVP |

---

## 1. Requirements traceability (official assignment)

| Requirement | Concrete deliverable | Evidence at demo |
|---|---|---|
| Listens and replies by voice | LiveKit room, STT→LLM→TTS, browser microphone/speaker | Hold a voice chat and interrupt it |
| Remembers across sessions | Persisted memories + selection/assembly in Postgres | New chat remembers favorite color or home city |
| External API | `get_weather(city)` tool using Open-Meteo | Ask for Riyadh weather, see real tool trace |
| Public deployment | HTTPS app URL, no manual registration | Fresh incognito browser opens demo |
| One deep dive | Conversational context programming | Exclude travel memories but retain budget |
| Explain technical choices | `README.md`, `docs/decisions.md`, `docs/limits.md` | Explain exactly which invariants are enforced |
| Code walkthrough | Pure context engine, adapter factories, typed state | Open tests and trace one model request |

**Proposed 2–3 sentence external API justification:**

> Sarjy uses Open-Meteo so a voice request can turn into a useful, verifiable real-world action rather than just a generated answer. Weather also provides a natural test of cross-session memory: Sarjy can remember a city and use it in a later session. Its low-friction API allows us to focus the deep dive on controllable context while still demonstrating reliable tool calling. 

**Caution:** Open-Meteo's free/non-commercial license and rate limits must be respected; attribute the service in the app and documentation. Don't represent its public free endpoint as an unrestricted commercial API.

---

## 2. Technology stack — explicit choices

| Layer | Technology | Hosting / runtime | Notes |
|---|---|---|---|
| UI | **SvelteKit** + adapter-node, **TypeScript** | **CranL** preferred; Vercel fallback if deployment compatibility slows Day 1 | Code-split Developer Mode; initialize browser audio only on the client |
| Styling | **Tailwind CSS** + **shadcn-svelte** / Bits UI primitives | Frontend bundle | Fast, consistent controls and accessible panels |
| Data/query UI | TanStack Query | Browser | HTTP cache; live session state remains separate |
| Local UI state | Svelte reactive state | Browser | Mode, active session, playback, pending command preview |
| Remote audio and agent client | LiveKit TypeScript client SDK | Browser + LiveKit Cloud | WebRTC for media; avoid streaming raw audio through FastAPI |
| Backend | **Python 3.12+**, **FastAPI**, Pydantic v2 | **CranL** (see §13); Cloud Run fallback | API owns auth, memory, context, tools, and sessions |
| Agent process | **LiveKit Agents 1.8.x Python**, vendor plugins | **LiveKit Cloud managed agents** | Separate long-running worker; pin versions after first successful run |
| Database | **PostgreSQL** (Supabase) + **pgvector** | Supabase managed | SQL migrations and narrow tenant/user ownership scopes |
| ORM / migrations | SQLAlchemy 2 async + Alembic (or focused SQL) | Backend | Use asyncpg and pooled DB connections as appropriate |
| Memory embeddings | OpenAI `text-embedding-3-small` | API | Explicit adapter; can replace later with a local embedding model |
| Normal chat LLM | DeepSeek V4.1 Flash | Direct API or LiveKit Inference | Start with low-latency non-thinking / streaming where supported |
| Optional chat LLMs | OpenAI **GPT-6 Luna**, **GPT-6.1 Terra**, optional **GPT-6.1 Sol** | OpenAI Responses API / LiveKit plugin | Model-specific tool calling and reasoning settings |
| STT | Deepgram, ElevenLabs, LiveKit Inference, optional browser | Provider service or client | Avoid using non-streaming batch transcription as live STT |
| TTS | **ElevenLabs Flash v2.5 (Arabic default)**; LiveKit Inference with verified Arabic voice; Deepgram for supported non-Arabic languages; optional browser | Provider service or client | **Never configure Deepgram Aura-2/Flux as Arabic TTS without a verified Arabic voice/model ID** |
| External tool | Open-Meteo weather + geocoding | Public API | Use proper source attribution |
| Logs / traces | Structured JSON logging, OpenTelemetry optional | Backend/agent logs | Per-turn IDs and timings; avoid logging private speech indiscriminately |
| Tests | pytest + Hypothesis + httpx + Playwright | Local / CI | Pure-domain property tests are a highlight |
| Package management | **uv**, pnpm | Local/CI | Lock Python and Node dependencies |
| Local dev | Docker Compose for Postgres (optional), cloud LiveKit sandbox | Developer machine | No local GKE required |

**Avoid new services until needed:** no Redis, no ClickHouse, no BigQuery, no Kubernetes installation, no distributed queue, no standalone vector database service. Postgres with pgvector is enough.

---

## 3. Provider landscape — what is actually interchangeable?

Four requested speech **selection modes** are not four equivalent API vendors:

| Selection | What it means | How it integrates | Billing key | Arabic status | Day-three intent |
|---|---|---|---|---|---|
| `livekit_inference` | Managed gateway for a supported STT/TTS model | `inference.STT/TTS` or documented descriptor | LiveKit credentials | **Model-specific** | Baseline if suitable model/voice verified |
| `deepgram_direct` | Deepgram's own STT/TTS endpoints | LiveKit Deepgram plugin | `DEEPGRAM_API_KEY` | **Nova-3 STT: Arabic supported. Aura-2 TTS: no Arabic. Flux TTS: no Arabic voice listed in the current official catalog.** | **Default STT; TTS only for verified non-Arabic languages** |
| `elevenlabs_direct` | ElevenLabs Scribe / Flash / multilingual TTS | LiveKit ElevenLabs plugin | `ELEVEN_API_KEY` | **Flash v2.5 (`eleven_flash_v2_5`) supports Arabic**; test selected voice, MSA, dialects and code-switching | **Default Arabic TTS; optional STT comparison** |
| `browser_local` | Models running in the user's browser | `VoiceRuntime` browser adapter + text-turn backend | None for local speech | Model/hardware dependent | Experimental, not P0 |

**Important current restrictions:** LiveKit **retired ElevenLabs from LiveKit Inference on 31 August 2026**, but the direct ElevenLabs LiveKit plugin remains available with your own API key. Deepgram Nova-3 supports Arabic STT; **Deepgram Aura-2 does not support Arabic TTS**, and **Deepgram Flux TTS currently lists 41 additional languages but no Arabic** in its official [voice catalog](https://developers.deepgram.com/docs/flux-tts/voices). This is a documented catalog limitation as of 11 November 2026, not a claim about all future releases. Deepgram TTS choices must be capability-gated; **fail configuration validation for `language=ar` instead of allowing a broken session or silently switching languages**. ElevenLabs lists Arabic explicitly for [Flash v2.5](https://elevenlabs.io/docs/overview/models), but Arabic voice quality still requires an actual playback test.

### 3.1 Internal LLM adapter catalog (not user settings)

| Internal adapter | Actual model/API ID | Main role | Initial settings |
|---|---|---|---|
| DeepSeek V4.1 Flash | `deepseek-flash` direct; `deepseek-ai/deepseek-v4.1-flash` via LiveKit | Preferred voice-chat baseline | Stream; explicitly disable thinking for every conversational turn |
| GPT-6 Luna | `gpt-6-luna` | Cost-efficient alternative / command parsing | Reasoning none/low when supported; stream |
| GPT-6.1 Terra | `gpt-6.1-terra` | Higher-quality alternative | Reasoning none/low as appropriate; Responses API |
| GPT-6.1 Sol | `gpt-6.1-sol` | Optional complex context-command experiments | Low/medium reasoning; not default for low-latency voice |
| GPT-5 family | Add a concrete *currently available* model only on request | Optional legacy comparison | Check exact ID, endpoint, and parameter support |

**Why not a single OpenAI-compatible wrapper for everything?** Although some endpoints share wire formats, OpenAI's Responses API and DeepSeek's compatibility interface differ in tool semantics, reasoning controls, token usage and streaming behavior. Hide these differences in **provider adapters**, not in the domain model. Never pass unsupported temperature/top-p or reasoning fields to a model. Model selection and provider selection are independent.

### 3.2 Arabic voice capability policy (release gate)

The application must treat **STT** and **TTS** language capabilities separately. A provider that understands Arabic audio does not necessarily synthesize Arabic speech.

| Provider/model | Role | Arabic status (11 Nov 2026) | MVP behavior |
|---|---|---|---|
| Deepgram Nova-3 Multilingual | STT | Supported; verify model/language settings, accents, and code-switching with real utterances | **Enabled; primary STT** |
| ElevenLabs `eleven_flash_v2_5` + chosen voice | TTS | Arabic officially listed; voice/dialect quality depends on selected voice | **Enabled after API smoke test; primary TTS** |
| ElevenLabs `eleven_multilingual_v2` + chosen voice | TTS | Arabic officially listed | Optional quality-vs-latency comparison |
| LiveKit Inference Arabic-capable TTS model | TTS | **Model-specific; must verify in live catalog and synthesize Arabic** | Fallback only after test |
| Deepgram Aura-2 | TTS | **Arabic not supported** | **Disabled for Arabic** |
| Deepgram Flux TTS | TTS | **Arabic not listed in official voice catalog; no verified `flux-*-ar` voice ID** | **Disabled for Arabic**, even if other pages broadly advertise multilingual TTS |
| Browser-local Nabra model | TTS | Arabic MSA, subject to device compatibility/quality | Experimental mode; never mandatory |

**Provider selection contract:** Before dispatching a LiveKit job, validate `(role, provider, model, voice_id, language, transport)` against an allowlisted capability registry. For `language=ar` and `provider=deepgram_direct` with `role=tts`, return a typed `UNSUPPORTED_LANGUAGE` configuration error with a suggestion to choose ElevenLabs or a *verified* managed Arabic TTS model. Reject unsupported combinations server-side even if the frontend controls are bypassed. Recheck provider support before the demo if the catalogs change.

**Testing gate:** Prove Arabic STT and TTS end-to-end with at least one Modern Standard Arabic sentence and one Saudi/Libyan dialect sample, plus an English phrase inside Arabic. Record voice ID, SDK version, and quality observations. Browser-local Arabic TTS is optional; never substitute it automatically during a production demo without an explicit mode setting and successful warm-up.

**Allowance accounting:** Deepgram's advertised **$200 starter credit is promotional/one-time**, not recurring. ElevenLabs' **10,000 monthly free credits are shared** among eligible products; using Flash TTS consumes credits, so check live plan balance and API rate before lengthy testing. Do not treat vendor credits as interchangeable or guaranteed for every account.

### 3.3 Recommended starting combinations

| Profile | STT | LLM | TTS | Use |
|---|---|---|---|---|
| **Arabic demo default** | Deepgram direct Nova-3 with explicitly tested Arabic setting | DeepSeek Flash direct | ElevenLabs `eleven_flash_v2_5` with tested Arabic-capable voice | Low-cost, credible Arabic end-to-end baseline |
| **Managed baseline** | LiveKit Inference Deepgram Nova-3 | LiveKit Inference DeepSeek Flash | LiveKit Inference *Arabic-capable supported model, chosen after voice check* | Quick setup and consolidated usage |
| **OpenAI comparison** | Same as default | GPT-6 Luna or GPT-6.1 Terra | Same as default | Isolate LLM model effects |
| **Deepgram speech comparison** | Direct Nova-3 | DeepSeek Flash | Direct Deepgram **verified English or another catalog-listed language voice** | Non-Arabic comparative experiment only; **disable Arabic** |
| **Local TTS experiment** | Deepgram STT | DeepSeek Flash | Browser Nabra-7M (Arabic MSA) | Research; separate runtime and fallback |
| **Local STT+TTS experiment** | Browser Whisper/ONNX STT | Cloud LLM via FastAPI | Browser Nabra or browser voices | Research only, capability gated |

**Mixed modes are allowed:** `STT=Deepgram`, `LLM=OpenAI`, `TTS=ElevenLabs`, for example. **For Arabic, the working default is `Deepgram Nova-3 → DeepSeek Flash → ElevenLabs Flash v2.5`.** Expose choices independently, but disallow invalid selections with explanatory UI messages and backend validation. Never let a model-selection fallback silently change the language of the assistant.

---

## 4. The provider abstraction: design the clean interface once

The main design goal is **configuration-driven composition**, not a god-class that tries to implement all vendor streaming protocols.

### 4.1 Single typed configuration

```python
from typing import Literal
from pydantic import BaseModel, Field

SpeechBackend = Literal[
    "livekit_inference", "deepgram_direct", "elevenlabs_direct", "browser_local"
]
LLMBackend = Literal["deepseek_direct", "openai_direct", "livekit_inference"]

class ModelChoice(BaseModel):
    provider: str
    model: str
    voice_id: str | None = None
    language: str = "en"

class PipelineConfig(BaseModel):
    runtime: Literal["livekit_remote", "browser_local"] = "livekit_remote"
    stt: ModelChoice
    llm: ModelChoice
    tts: ModelChoice
    turn_detection: str = "default"
    max_output_tokens: int = Field(default=128, ge=64, le=1024)
    revision: int = 1
```

The config is **validated against a provider capability registry** before any session starts. `provider`/`model` strings are not arbitrary user input: allowlisted and bound to verified accounts, deployment capabilities, languages, and transport type.

### 4.2 Two layers of abstraction

**Layer A — server-side LiveKit adapters** (do not reimplement STT/TTS streams unnecessarily):

```text
config -> validate capabilities -> resolve backend adapter -> build LiveKit AgentSession
                                   ├── STT: Inference / Deepgram / ElevenLabs
                                   ├── LLM: DeepSeek / OpenAI / Inference
                                   └── TTS: Inference / Deepgram / ElevenLabs
```

For remote providers, the factory produces native LiveKit SDK provider instances:

```python
# Schematic only: pin installed LiveKit version and check parameter names.
from livekit.agents import AgentSession, inference
from livekit.plugins import deepgram, elevenlabs, openai

STT_FACTORIES = {
    "deepgram_direct": lambda c: deepgram.STT(model=c.model, language=c.language),
    "elevenlabs_direct": lambda c: elevenlabs.STT(model=c.model),
    "livekit_inference": lambda c: inference.STT(model=c.model),
}

TTS_FACTORIES = {
    "deepgram_direct": lambda c: deepgram.TTS(model=c.model),
    "elevenlabs_direct": lambda c: elevenlabs.TTS(model=c.model, voice_id=c.voice_id),
    "livekit_inference": lambda c: inference.TTS(model=c.model),
}

# LLM_FACTORY independently selects DeepSeek direct, OpenAI Responses,
# or LiveKit Inference; browser_local is rejected in this server factory.
```

In practice, provider constructors and supported model IDs vary by installed SDK version; model-specific options must be translated only inside the corresponding adapter. Use `~1.8` LiveKit plugin docs as the starting reference, not as a forever guarantee.

**Layer B — frontend runtime boundary**:

```typescript
type SpeechRuntime = "livekit_remote" | "browser_local";

interface VoiceRuntime {
  connect(session: SessionConfig): Promise<void>;
  startMicrophone(): Promise<void>;
  stopMicrophone(): Promise<void>;
  interrupt(): Promise<void>;
  sendText(text: string): Promise<void>;
  disconnect(): Promise<void>;
  subscribe(handler: (event: VoiceEvent) => void): () => void;
}
```

- `LiveKitVoiceRuntime` uses WebRTC to a LiveKit room and delegates recognition and synthesis to the remote worker.
- `BrowserSpeechRuntime` uses AudioWorklet / Web Worker / WASM/WebGPU and sends **recognized text** to an authenticated backend streaming endpoint; it plays **locally synthesized audio** using an explicit playback queue.
- Both emit normalized events (`speech.started`, `transcript.final`, `assistant.text.delta`, `assistant.audio.started`, `assistant.interrupted`, `error`) with `conversation_id`, `turn_id`, `response_id`, and sequence/epoch IDs.

**Why two runtimes?** Browser-local inference changes **where audio is produced and consumed**, not merely which vendor API key is used. Treating it as a server-side plugin would obscure transport, privacy, CPU/GPU, jitter and cancellation concerns. No attempt is made to run a browser GPU engine in a remote FastAPI worker.

### 4.3 Capability registry / compatibility matrix

Each adapter advertises:

```python
class ProviderCapabilities(BaseModel):
    role: Literal["stt", "llm", "tts"]
    languages: tuple[str, ...]
    streaming: bool
    supports_partial_results: bool = False
    supports_interruptions: bool = True
    supports_tool_calls: bool = False
    runtime: Literal["server", "browser"]
    requires_key: str | None = None
    known_limitations: tuple[str, ...] = ()
```

Internal diagnostics can list **available**, **not configured**, **unsupported language**, **experimental**, and **unavailable on this device**. Providers with no credentials or incompatible languages must not be presented as if they will work.

### 4.4 Swapping rules

- Internal provider configuration is resolved when a **voice session starts**; users select approved voices, not providers. Changing STT/TTS mid-audio requires rebuilding or restarting media pipeline; show *“applies on next voice session”* rather than silently dropping audio.
- LLM may be switchable at a turn boundary if the SDK explicitly supports it; otherwise switch on next session too.
- Memory/context state is **independent** of any provider choice. Changing models does not erase conversations.
- Expose a small `POST /v1/pipelines/validate` endpoint before the frontend offers *Start voice chat*.
- Capture actual selection in every turn/snapshot for reproducibility.

---

## 5. Architecture and flow

```text
                   Browser: SvelteKit + TypeScript
 ┌──────────────────────────────────────────────────────────────┐
 │ Simple Mode: chats / transcript / mic                       │
 │ Developer Mode: context search / commands / diffs / metrics │
 │        LiveKitVoiceRuntime  OR  BrowserSpeechRuntime         │
 └──────────────┬───────────────────────┬───────────────────────┘
                │ HTTPS JSON/SSE         │ WebRTC (remote mode)
                ▼                        ▼
        ┌────────────────┐       ┌──────────────────┐
        │ FastAPI         │       │ LiveKit Cloud    │
        │ auth, sessions  │◄─────►│ rooms + agents   │
        │ context compiler│       │ STT → LLM → TTS  │
        │ memory & tools  │       │ event/usage hooks│
        └────────┬───────┘       └────────┬─────────┘
                 │                        │ provider plugins
                 ▼                        ▼
      ┌──────────────────────┐   ┌────────────────────────┐
      │ Supabase PostgreSQL  │   │ Deepgram / ElevenLabs  │
      │ users conversations  │   │ DeepSeek / OpenAI      │
      │ context records +   │   │ LiveKit Inference      │
      │ pgvector snapshots  │   └────────────────────────┘
      └──────────────────────┘

 Browser-local experiment: Web Worker STT → /v1/chat/stream → LLM → browser TTS
                              (no server-side STT or TTS on this path)
```

### Responsibilities

**FastAPI**: trusted identity; authorization; chat / session CRUD; API tool access; context compiler; embedding + retrieval; command proposal/commit/undo; version checks; metadata; provider credential handling; optionally text streaming for browser-local mode.

**LiveKit worker**: room connection; VAD, turn-taking, STT, LLM, TTS; interruption/cancellation; requests the authoritative compiled context from FastAPI; forwards final turns/metrics; invokes tools through authenticated services. Worker events must not be mistaken for the sole durable source of truth.

**Frontend**: rendered conversation; mic permissions; device capability checks; form controls; context diff UX; audio state; connectivity; browser-local speech worker when enabled.

**PostgreSQL**: durable user/chat/memory records; exclusions; revisions; command previews and audit log; per-turn model-request manifests; usage summaries. `pgvector` is an extension of PostgreSQL, not a second database.

### One authoritative conversation state

Regardless of audio mode:

1. Generate `turn_id` on the server.
2. Finalize user's transcript (or typed message).
3. Load permitted chat history + memory candidates under a **consistent context revision**.
4. Apply active exclusions; compile the provider-bound message set, record IDs and prompt hash.
5. Stream the model response and capture tool events.
6. Persist final turn status (`completed`, `interrupted`, `failed`) plus usage.
7. Apply approved memory extraction at a safe transaction boundary.

The LiveKit `ChatContext` object is **not** automatically the entire persistent memory store, and an internal summary can reintroduce an excluded fact. Centralize prompt assembly at the model boundary. Capture the **actual serialized/provider-bound request** wherever the SDK supports it; otherwise label the stored snapshot precisely as **the canonical pre-provider message list**, and do not claim byte-for-byte provider visibility.

### LiveKit implementation seam for correct context editing

Use LiveKit's official **`on_user_turn_completed`** hook for per-turn memory retrieval and the **`llm_node`** override to inspect/replace the effective model input immediately before inference. The official SDK also exposes **`update_chat_ctx(...)`** to replace the internal chat context. Pick one authoritative strategy and write an integration test proving that an excluded memory cannot linger in `AgentSession`'s existing history:

- Start from the user's current finalized turn and the **server-compiled, revision-stamped** canonical messages.
- Build a **fresh** `ChatContext` for the current request, with required recent turns, permitted memories and tool results; avoid repeatedly appending retrieved memories to a persistent context without eviction.
- Make the worker acquire/acknowledge the context revision before LLM generation. If the user confirms an exclusion while an answer is already being generated, **cancel/restart or apply to the next turn with an explicit UI indication**; never silently claim the currently running request changed retroactively.
- Store the exact input passed to the LiveKit LLM adapter where available, along with the context revision and included record IDs. If internal SDK/provider formatting remains inaccessible, label this as **effective SDK `ChatContext`** rather than claiming provider-wire fidelity.
- Exclusion covers persisted source records and tracked derivations in new requests, not weights or already-consumed model state. A new turn should not inherit stale retrieved context from an older in-memory object.

See [LiveKit pipeline nodes and `llm_node`](https://docs.livekit.io/agents/logic/nodes/) and [LiveKit chat context / `update_chat_ctx`](https://docs.livekit.io/agents/logic/chat-context/).

---

## 6. Persistent memory and context-engine design

### 6.1 Four distinct layers

1. **Transcript/history**: exact utterances by conversation, immutable after commit except explicit record-management operations.
2. **Persistent memory store**: extracted atomic facts/preferences with source refs, embeddings, timestamps, revision/status.
3. **Active context**: selected current-turn messages, summaries, memories and external tool results.
4. **Compiled model input**: ordered, versioned, model-specific messages actually sent (or canonical pre-adapter representation when exact request unavailable).

Keep a visible label on every record: `system`, `recent_turn`, `memory`, `summary`, `tool_result`, `manual_note`. Protected system rules and authentication context must never be toggleable through user speech.

### 6.2 Candidate selection and semantics

- Memories should be **atomic**: *“Prefers evening appointments”* separate from *“Riyadh trip budget is 3,000 SAR”*. Atomicity allows *“forget trip but retain budget”* to work without destructively editing arbitrary paragraphs.
- Embed every saved memory (using OpenAI embeddings or replaceable adapter) and record its original source turn.
- Query with **semantic vector similarity + simple keyword filtering + an optional LLM or deterministic relevance recheck**. Return candidates with IDs and evidence—not an automatic deletion decision.
- When asked to exclude a broad topic, distinguish **matched records** from **definitely exhaustive semantic removal**. Vector similarity cannot prove it found every mention or every paraphrase.
- A memory might be copied into a conversation summary. Either regenerate summaries under new exclusions, track derived dependencies, or block the assumption of total semantic erasure. For the MVP, prefer no derived long-term summaries and mark conversation text separately; show the exact selected records and prompt preview.

### 6.3 Typed operations

A voice/text intent parser produces one of:

```text
SearchContext(query)
ProposeExclude(ids, scope = current_conversation)
ConfirmExclude(proposal_id, expected_revision)
RestoreExclusion(operation_id, expected_revision)
InspectContext(turn_id | next_turn)
```

Permanent deletion is **out of scope** for the primary three-day experience. Optionally expose manual delete in a later version with separate retention semantics and confirmation. A command like *“forget permanently”* must not claim complete erasure if old conversation transcripts, logs, traces, provider state or backups still contain the same fact.

### 6.4 Two-phase mutation protocol

1. **Interpret** the spoken request into a strict validated schema (LLM output is untrusted).
2. **Resolve** target candidate IDs and read accessible record data.
3. **Preview** proposed exclusions with source and proposed scope; never act on similarity alone.
4. **Confirm** exact IDs. Backend checks user ownership, protected flags, revision, and duplicates.
5. **Commit atomically** via SQL transaction with optimistic concurrency (`expected_revision`).
6. **Recompile** next-turn context and store a diff/manifest.
7. **Undo** by applying an inverse operation against a versioned revision; do not overwrite intervening edits.

### 6.5 A minimal pure context engine

```python
@dataclass(frozen=True)
class ContextState:
    revision: int
    items: tuple[ContextItem, ...]
    excluded_ids: frozenset[str]


def exclude_ids(state: ContextState, ids: frozenset[str]) -> ContextState:
    allowed = {
        item.id for item in state.items
        if item.id in ids and not item.protected
    }
    return replace(
        state,
        revision=state.revision + 1,
        excluded_ids=state.excluded_ids | allowed,
    )
```

**Reality check:** A pure function preserves *logic*; authorization, consistency and persistence must still be enforced by the DB transaction. Context revisions must not advance for rejected commands; no-op revisions can be a deliberate documented policy.

### 6.6 Suggested context manifest

```json
{
  "turn_id": "turn_456",
  "context_revision": 12,
  "pipeline_id": "arabic-default-v1",
  "included_record_ids": ["m1", "m4", "turn_44"],
  "excluded_record_ids": ["m2", "m3"],
  "canonical_prompt_sha256": "...",
  "token_estimate": 3400,
  "provider_request_capture": "canonical_pre_adapter"
}
```

A hash is an audit aid, not proof that a language model never used a fact from an earlier turn. Excluded information should not be present in newly compiled messages by ID or by tracked dependency; do not claim semantic erasure of all possible paraphrases.

---

## 7. Concrete schema and migrations

Implement with migrations and foreign keys. Suggested logical tables:

| Table | Key fields / indices | Notes |
|---|---|---|
| `identities` | `id`, `kind`, `created_at`, `expires_at` | Demo identity or signed-in user |
| `conversations` | `id`, `owner_id`, `title`, `created_at`, `updated_at` | Index `(owner_id, updated_at)` |
| `voice_sessions` | `id`, `conversation_id`, `room_name`, `pipeline_id`, `status` | One call/session; optional recording metadata |
| `turns` | `id`, `conversation_id`, `role`, `text`, `status`, `started_at`, `ended_at` | Track interruptions and timestamps |
| `memories` | `id`, `owner_id`, `content`, `embedding vector(N)`, `source_turn_id`, `status`, `updated_at` | Dimension `N` matches embedding model; user ownership index |
| `context_revisions` | `id`, `conversation_id`, `revision`, `created_at`, `created_by` | Unique `(conversation_id, revision)` |
| `context_exclusions` | `id`, `owner_id`, `conversation_id`, `memory_id`, `operation_id`, `scope`, `active` | Scope current conversation for MVP |
| `context_operations` | `id`, `owner_id`, `conversation_id`, `command_type`, `payload`, `revision_before`, `revision_after` | Idempotency key, outcome, time |
| `context_snapshots` | `id`, `turn_id`, `revision`, `canonical_messages`, `manifest`, `pipeline_config` | Retention policy; no secrets in snapshots |
| `tool_calls` | `id`, `turn_id`, `name`, `arguments`, `result_summary`, `status` | Prevent duplicate side effects |
| `turn_metrics` | `id`, `turn_id`, `stt_ms`, `eot_ms`, `llm_ttft_ms`, `tts_ttfb_ms`, `ttfa_ms`, `usage_json` | Null means *not measured* |

**Extensions and indexes:** enable `pgvector`, `pgcrypto` as needed; use HNSW on embeddings if sample size justifies it, otherwise simple indexed/filtered cosine search is enough. Verify `embedding vector(N)` dimension and compare apples to apples when changing embedding models; do not mix vectors with incompatible dimensions.

**Memory lifecycle:** explicit stated preferences save immediately after confirmation; implicit extraction may run after a completed turn with consent/visibility. Contradictory preferences should be updated via source-aware decisions, not simply appended until both are retrieved. Use deterministic demo examples.

---

## 8. Backend routes and schemas

### Application

```text
GET    /healthz
POST   /v1/demo-session                  -> scoped demo identity/cookie
GET    /v1/conversations
POST   /v1/conversations
GET    /v1/conversations/{id}
POST   /v1/conversations/{id}/messages   -> typed message / context command routing
GET    /v1/conversations/{id}/turns
POST   /v1/voice/sessions                -> validate config / room / dispatch / scoped token
POST   /v1/voice/sessions/{id}/end
```

### Memory + context

```text
GET    /v1/memories?query=...&limit=20
GET    /v1/conversations/{id}/context
GET    /v1/turns/{turn_id}/context-snapshot
POST   /v1/conversations/{id}/context/proposals
POST   /v1/conversations/{id}/context/commit
POST   /v1/conversations/{id}/context/undo
GET    /v1/conversations/{id}/context/operations
```

### Internal provider validation + read-only metrics

```text
GET    /v1/pipelines/providers      -> capability/availability registry (NO secrets)
POST   /v1/pipelines/validate
GET    /v1/pipelines/presets
GET    /v1/turns/{id}/metrics
GET    /v1/voice/sessions/{id}/metrics
```

### Internal worker-to-backend (never public anonymous endpoints)

```text
POST   /internal/agent/turn/start          -> creates immutable turn ID/context revision
POST   /internal/agent/turn/finalize       -> persist final transcript/response
POST   /internal/agent/tool/weather        -> authenticated tool call
POST   /internal/agent/metrics             -> persist measured events
```

Use **short-lived worker auth or signed service credentials**, an allowlisted room/agent ID, and ownership checks. Provide idempotency keys on write endpoints. The public browser must not be able to spoof agent events or other users' conversation IDs.

### Command preview example

```json
{
  "command": "exclude_topic",
  "query": "everything about the Riyadh trip except its budget",
  "matches": [
    {"id": "m2", "content": "Travel dates for Riyadh", "source_turn": "t17"},
    {"id": "m3", "content": "Hotel preference in Riyadh", "source_turn": "t18"}
  ],
  "preserved": [
    {"id": "m4", "content": "Riyadh trip budget", "reason": "explicit keep exception"}
  ],
  "expected_revision": 11
}
```

A separate confirm request references the server-created proposal. Reject stale revisions and never trust a client-supplied list of arbitrary record IDs without reauthorizing.

---

## 9. Frontend design

### Simple mode

```text
┌──────────────────────┬───────────────────────────────────────────────┐
│  + New chat          │ Sarjy                         Simple [Dev]  │
│  Recent conversations│───────────────────────────────────────────────│
│  • Travel            │ User: Remember my home city is Riyadh.       │
│  • Weather           │ Sarjy: Got it.                               │
│  • Coding            │                                               │
│                      │ Listening / Thinking / Speaking              │
│                      │                                               │
│                      │ [ Type here ... ]                [ Mic ]      │
└──────────────────────┴───────────────────────────────────────────────┘
```

### Developer mode

```text
┌──────────────┬──────────────────────────────┬────────────────────────────────┐
│ Chats        │ Same live chat + transcript  │ Tabs: CONTEXT / REQUEST / STATS│
│              │                              │                                │
│              │ Selected turn highlights     │ Search memories semantically   │
│              │ context used for that turn   │ [Everything about travel...]  │
│              │                              │                                │
│              │ Voice command                 │ [x] Travel dates, source #12  │
│              │ "Forget travel except budget"│ [ ] Budget, source #13 KEEP  │
│              │                              │                                │
│              │ [Confirm exclusions]         │ Diff before/after + Undo      │
└──────────────┴──────────────────────────────┴────────────────────────────────┘
```

**Interaction requirements:**

- Mode switch does not restart LiveKit or change AI behavior. Developer-only mutations require authorization, not just CSS visibility.
- Context search results distinguish **persistent store**, **retrieved for turn**, **present in compiled prompt**, and **excluded**.
- Context diffs show record IDs and excerpts; clear confirmation before applying.
- Voice-command mode should show an explicit *“Context edit requested”* badge/status, **not** silently send the instruction as ordinary chat text.
- Use skeletons for provider initialization and a working state for unsupported browser-local capability.
- Keep developer stats small: current STT/LLM/TTS selections, last-turn latency, model tokens, context size estimate, provider errors.
- State how estimated token counts differ from provider-reported counts.
- Local model download must not block the normal page; load only when selected and cache weights when possible.

---

## 10. Web audio, interruptions, and correctness

### Remote path (primary)

1. Browser requests room and worker dispatch through FastAPI.
2. LiveKit carries mic audio; agent constructs STT/LLM/TTS from validated config.
3. Partial and final transcripts arrive as structured events.
4. End-of-turn detector commits the user turn; model request uses compiled context revision.
5. Response audio streams through LiveKit; UI displays speech state.
6. User interrupts: stop playback, cancel/mark invalid pending assistant generation, discard stale events by response epoch, preserve actually completed tool actions.
7. On disconnect, finalize session state and allow safe reconnect or new room.

### Browser-local path (experimental)

- `getUserMedia` + AudioWorklet → Web Worker STT inference → final text → authenticated `/v1/chat/stream` (SSE or WebSocket) → local text chunk buffering/TTS → Web Audio playback.
- WebGPU detection, worker startup, model download, and cached weight reuse are part of the capability check. Do not run ONNX inference on the main UI thread.
- Minimum browser feature gate: usable memory, supported WASM/WebGPU runtime, microphone permission, compatible model language.
- **Local STT**: browser-compatible multilingual Whisper/ONNX is a research candidate, but streaming may require repeated buffered inference; Arabic recognition must be benchmarked.
- **Local TTS**: Nabra-7M-Distill (~30MB browser model) is an Arabic (MSA) research candidate; native `speechSynthesis` is a zero-download baseline but quality and availability vary by OS/browser.
- Browser-local speech removes per-minute STT/TTS calls but can worsen first-visit and first-audio latency. Design explicit fallback to remote provider.
- Do **not** claim end-to-end audio is processed locally: transcripts/text still reach the cloud LLM and tool server.

---

## 11. External API: Open-Meteo implementation

**Tool name:** `get_current_weather(city: str | None, country: str | None)`.

- If city is explicitly supplied in the current conversation, prefer it.
- Otherwise, derive a city **only from currently included, permitted memory/context records**.
- If an earlier city memory was excluded, do not bypass the context engine by querying raw stored profile information.
- If no city is available, ask the user for one.
- Use Open-Meteo geocoding to resolve ambiguous place names; ask clarification where multiple plausible cities exist.
- Call weather forecast/current endpoint; format a factual answer with location, temperature, wind/conditions and observation timezone as available.
- Handle network errors with an explicit truthful response; never invent current weather.
- Store tool call input/result summary with `turn_id` for the developer inspector; do not silently persist sensitive location in a different store.
- Attribute Open-Meteo as required by its terms and rate limits.

**Functional test tied to memory:** First session: “I live in Riyadh.” Second session: “What's the weather?” The assistant uses the permitted remembered city. Issue: “Exclude where I live for this chat.” Ask again; the assistant must ask for a city rather than reusing the excluded memory (unless another explicit valid city was just provided).

---

## 12. Authentication, tenant-like isolation, and privacy

The reviewer must open the deployed URL without registration. Prefer **anonymous Supabase Auth** if suitable for the chosen integration or a **server-minted demo identity bound to an HttpOnly Secure SameSite cookie**, with short expiry and restrictive quotas.

- Demo users get independent `owner_id` values; never share one public demo identity across all visitors.
- `conversation_id`, `memory_id` and `turn_id` must be authorized server-side for *every* read and write.
- Block clients from setting arbitrary room names or reading others' agent dispatch data.
- Context snapshots are sensitive. Restrict access by owner and avoid storing raw audio by default.
- Keep all provider API keys strictly server-side. Browser local speech models do not need keys; browser clients only receive scoped LiveKit tokens.
- Add per-demo-user voice-minute/session caps and API rate limits. Prevent public demo abuse.
- Developer diagnostics are read-only; model/provider selection and paid inference settings are internal only.
- Audio inputs, retrieved memories and tool results are **untrusted data**. Context commands require authorization before execution.
- Do not promise Saudi data residency for this personal PoC; it would require verifying all regional endpoints, providers, logs and backups.

---

## 13. Hosting and deployment — CranL-first architecture

### 13.1 Selected topology for the first working build

```text
SvelteKit (CranL App 1, HTTPS) ── HTTPS ──► FastAPI (CranL App 2, HTTPS)
             │                                      │
             │ WebRTC                               ▼
             └──────────────────► LiveKit Cloud   Supabase PostgreSQL + pgvector
                                        │              ▲
                                        └─ Agent worker ┘
                                           (LiveKit Cloud)
```

**Do not host a long-running LiveKit agent on a request-scoped serverless handler.** Use LiveKit Cloud agent deployment. LiveKit rooms and model-provider APIs remain external services. The SvelteKit frontend and FastAPI backend are ordinary CranL apps (not CranL cloud-agent or sandbox machines).

### 13.2 CranL specifics, cost, and fallback

CranL documentation currently advertises GitHub or Docker deployments, automatic TLS and `*.cranl.net` app URLs, environment variables, logs, app health tracking, and managed databases.

| CranL plan | Price | Combined apps + databases | Region access | Fit for Sarjy |
|---|---:|---:|---|---|
| **Free** | **$0/month** | **2** | Netherlands | Two CranL apps (SvelteKit and FastAPI) **if DB stays on Supabase** |
| Basic | $4.99/month | 3 | Excludes MENA | Room for one CranL-managed DB, but pgvector needs verification |
| Pro | $9.99/month | 20 | Includes MENA | Optional Saudi hosting for app processes, **not** proof of end-to-end data residency |

Free includes **$2/month of sandbox credit**, which is separate from guaranteed inference or application capacity. Check app CPU/RAM limits and actual uptime behavior from within the account; do not confuse sandbox credits with permanently free production compute.

**Day 1 deployment smoke test:** Deploy a minimal SvelteKit route and FastAPI `/healthz` on CranL immediately. If either deployment fails or platform limitations threaten the three-day deadline, use **CranL for FastAPI + Vercel/Cloudflare for SvelteKit** while retaining the same API interface and two-service architecture. Google Cloud Run is a secondary backend fallback aligned with the employer's stack; Railway/Render/Koyeb are optional alternatives, not the recommended default.

CranL managed PostgreSQL is an option, but **pgvector extension support was not confirmed in the published documents checked**. Keep Supabase PostgreSQL + pgvector in the v3 baseline, rather than silently dropping semantic search or assuming the extension is installed. Cloud-provider region choice does not imply AI-provider data residency.

CranL reference documentation: [website/pricing](https://cranl.com/), [sign-up and plan limits](https://docs.cranl.com/getting-started/sign-up.html), [deploy quickstart](https://docs.cranl.com/getting-started/quickstart.html), [applications](https://docs.cranl.com/platform/applications.html), [managed databases](https://docs.cranl.com/platform/databases.html).

### 13.3 Frontend alternatives

- **CranL** is preferred for SvelteKit if its Git/Docker build passes the Day 1 smoke test. Serve with HTTPS via its generated app URL; check environment variables, build output, and any SSR constraints.
- **Vercel Hobby** is the fallback for SvelteKit, but its personal non-commercial restriction applies. Verify eligibility for a recruiting demo before using it.
- **Cloudflare** is aligned with the company's frontend stack. Use a currently supported SvelteKit deployment path and test the app thoroughly; the runtime compatibility work can take valuable day-one time. For a static-exportable app, Cloudflare Pages is an alternative, but check the app's server features.
- **Netlify** can be another practical free-tier frontend alternative if the SvelteKit setup is tested.

### 13.4 Database and LiveKit

- **Supabase Free:** PostgreSQL, auth, pgvector, 500 MB DB; projects can pause after a week of low activity. Confirm active just before reviewer visit.
- **LiveKit Cloud Build:** free starting plan with limited agent-session usage and about **$2.50 inference credits**, not unlimited model calls. BYO vendor credentials may permit more speech testing without managed inference charges, subject to LiveKit room/agent limits.
- **LiveKit agent deploy:** `lk cloud auth` → `lk agent create` (check docs for installed CLI). Keep separate secrets and explicit callback URL to FastAPI.

### 13.5 Deployment steps (finish Day 1)

1. Create repository and isolated provider accounts / keys.
2. Create Supabase project; apply SQL migrations and enable pgvector.
3. Bootstrap LiveKit Cloud project and voice agent from current official starter; test from the LiveKit console.
4. Deploy the FastAPI image to **CranL**; verify `/healthz`, HTTPS, resource limits and secret env vars. Keep a Cloud Run-compatible Dockerfile as fallback.
5. Deploy SvelteKit as a **second CranL app**; configure `PUBLIC_API_URL`, `PUBLIC_LIVEKIT_URL`; no secrets in `PUBLIC_*`. If that blocks progress, move only the frontend to Vercel/Cloudflare.
6. Set CORS to the deployed frontend origin (and local dev origin only when needed).
7. Create demo identity API and session token issuance; connect browser microphone to LiveKit room.
8. Run an actual voice + weather session over the production URL.
9. Fix any callback reachability, room dispatch, cold-start, or OAuth redirect issues immediately.
10. Save the live URL in README on Day 1; add a simple status banner if provider credits are low.

### 13.6 Environment variables (sample)

```dotenv
# Backend only (never exposed as public frontend variables)
DATABASE_URL=postgresql+asyncpg://...
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...   # only if required; never shipped to browser
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
DEEPGRAM_API_KEY=...
ELEVEN_API_KEY=...
EMBEDDING_MODEL=text-embedding-3-small
DEFAULT_LLM_PROVIDER=deepseek_direct
DEFAULT_LLM_MODEL=deepseek-flash
DEFAULT_STT_PROVIDER=deepgram_direct
DEFAULT_STT_MODEL=nova-3
DEFAULT_TTS_PROVIDER=elevenlabs_direct
DEFAULT_TTS_MODEL=eleven_flash_v2_5
DEFAULT_LANGUAGE=en
DEMO_SESSION_LIMIT_MINUTES=10
DEMO_RATE_LIMIT=...
APP_BASE_URL=https://...
CORS_ORIGINS=https://...

# Frontend public configuration (NO SECRETS)
PUBLIC_API_URL=https://api.example.com
PUBLIC_LIVEKIT_URL=wss://your-project.livekit.cloud
```

Never put provider keys or user-profile secrets into anonymous LiveKit room metadata or client app bundles.

---

## 14. API keys, what they buy, and budget

| Service / key | Required? | Why | Free / price considerations |
|---|---|---|---|
| **LiveKit Cloud** API key + secret | **Yes** | WebRTC rooms, agent dispatch, optional managed inference | Build has no fixed subscription; $2.50 inference credits and separate session limits |
| **DeepSeek** API key | **Yes for direct route** | `deepseek-flash` primary LLM | Paid token API; low unit cost; price changes by peak/off-peak windows |
| **OpenAI** API key | **Yes for model comparison and embeddings** | GPT-6 Luna/Terra and `text-embedding-3-small` | Pay-as-you-go; embedding cost small; check minimum top-up |
| **Deepgram** API key | **Yes for direct STT and non-Arabic TTS experiment** | **Nova-3 Arabic STT**; TTS only in listed voice languages | **$200 one-time starter credits**; not necessarily recurring |
| **ElevenLabs** API key | **Yes for default Arabic TTS; optional STT test** | **`eleven_flash_v2_5` Arabic speech generation**; optional Scribe realtime STT | **10,000 free credits/month**, **shared across eligible services**; check the current API credit rate and monthly balance |
| **Supabase** project keys / DB URL | **Yes** | Postgres, pgvector, demo auth | Free tier with limits; inactivity pause risk |
| **Open-Meteo** | **Normally no key for eligible free noncommercial endpoint** | Weather + geocoding demonstration | License, attribution and rate limits matter |
| **CranL** account | **Yes** | Frontend + FastAPI deployment | Free has two combined apps/databases in Netherlands; paid Basic/Pro are alternatives |
| **Browser-local models** | **No speech API key** | STT/TTS inference on device | Weight distribution, client compute, battery and quality are real costs |

**Important distinction:** A LiveKit Cloud key does not make all providers free, and a LiveKit plugin can use an external vendor key even when managed LiveKit Inference does not offer that model.

### Cost discipline

- **Day 3 Arabic candidate:** Deepgram Nova-3 STT using introductory credits + ElevenLabs Flash v2.5 TTS using the shared monthly free allocation. Measure actual consumption per 5-minute call, and avoid wasting speech credits on text-only context tests.
- Keep a **verified Arabic TTS fallback** in the capability registry only if it passed a real synthesis smoke test. If ElevenLabs quota runs out and no fallback passed, surface a clear TTS-unavailable error; do **not** silently route Arabic through Deepgram English TTS.
- Keep text-only development for memory engine and context commands; this consumes minimal LLM tokens and no speech calls.
- Enforce short responses (`max_output_tokens`), short sessions, output character caps, limited concurrent demo rooms, and modest context budgets.
- Track **usage by actual vendor**, not merely totals: STT seconds, LLM tokens/cache hits, TTS characters, embeddings tokens and API calls.
- Compare commercial/provider pricing only when reported on the official live rate card. Most costs here should be negligible for the PoC if usage is constrained, but free credit eligibility is account-dependent.

### Initial cash recommendation

With free or starter credits, expect an **extremely low initial usage bill**, but do not promise absolute $0. A practical **$10–$25 experimentation budget** plus a small hosting contingency (CranL Basic $4.99/month if needed) is sensible; the first live 5-minute call will establish actual consumption. A paid LiveKit plan can be materially more expensive than that, so don't subscribe until needed.

---

## 15. Benchmark plan: decide speech providers empirically, not by reputation

This experiment informs provider defaults; **it is not our formal deep dive**.

### 15.1 Fixed corpus

Record or generate a small, reproducible corpus of 8–12 utterances:

- English short question and long explanation request.
- Arabic MSA question.
- Saudi Arabic and Libyan Arabic informal requests (if suitable speakers available).
- Arabic-English code-switching, English technical terms inside Arabic.
- A user correction and a deliberate two-second pause.
- Numeric order number, city name, and technical proper noun.
- Quiet-room sample and a deliberately noisy sample.

Use identical content and, where possible, the **same audio sample** for STT comparisons. Run end-to-end conversations separately because agent state and output speech length may differ.

### 15.2 Metrics

| Metric | Definition | How to measure |
|---|---|---|
| Cold-start time | Time from choosing provider to ready-to-transcribe/speak | Browser SDK + provider setup timestamps |
| Model download time | First-run local model weight transfer | Browser performance timings |
| STT partial latency | Speech onset → first useful partial | Provider events / monotonic timers |
| STT finalization | End of user speech → committed transcript | VAD/final STT events |
| STT accuracy | WER/CER; numeric/intent correctness for Arabic | Fixed reference transcripts + manual review |
| LLM TTFT | Model request sent → first substantive text | Server monotonic clock |
| TTS first byte/frame | Text available → first synthesized audio data | Vendor/worker hooks |
| **Time to first audible answer** | End of user turn → speaker actually plays meaningful speech | Browser timestamp; separate acknowledgment filler |
| Interruption stop time | User starts speaking → old audio stops | Browser and LiveKit events |
| TTS quality | Arabic intelligibility, pronunciation, naturalness | Blind listening ratings, small sample |
| Cost | API usage per fixed task | Provider usage + current rate card |
| Memory / CPU | Browser device resource use in local mode | JS memory where exposed + browser/devtools profiling |

Store repetitions, device/browser, language, model ID, provider/region, network conditions, and sample IDs. Show p50/p95 where enough runs exist; with tiny samples, report raw observations and confidence limits instead of pretending statistical significance.

### 15.3 A fair test order

1. Verify one managed LiveKit baseline.
2. Keep the LLM constant and compare STT across Deepgram direct vs LiveKit-hosted Deepgram vs ElevenLabs direct; check whether the *underlying model* differs.
3. Keep STT and LLM constant and compare Arabic TTS ElevenLabs vs a **verified Arabic-capable** LiveKit model.
4. Deepgram Aura-2 and Flux **are not eligible for the Arabic TTS comparison under the currently documented voice catalog**. If desired, compare an English/catalog-listed Flux voice in a separate non-Arabic benchmark; re-enable Arabic only on verified catalog and API evidence.
5. Test local TTS on the actual reviewer-like browser/hardware only if time remains.
6. Add local STT after the product is already feature-complete.
7. Decide a **working default**, one **tested fallback**, and an **unsupported** list. Do not claim local is lower latency merely because it removes network calls.

### 15.4 UX for comparisons

Internal benchmark reports show provider/model, capability, streaming and cold-start risk; public settings do not expose provider selection. Include a compact test screen on day four *only if it already exists*; otherwise document the benchmark results and open scripts in the repository.

---

## 16. Functional programming, property testing, and correctness

Use **functional core / imperative shell**:

- **Pure:** context selection, record filtering, exclusion/restore transforms, validation of typed commands, prompt compilation, diff generation and estimated token budgeting.
- **Effectful:** embedding calls, semantic search DB IO, weather API, token signing, LiveKit session events, audio playback, logging.

### Essential Hypothesis properties

1. Exclusion idempotence (`exclude(exclude(S, X), X)` keeps effective visibility unchanged).
2. Visible context contains no ID in the active exclusion set.
3. Protected records cannot be excluded by a user-issued command.
4. Tenant/user isolation: operations on A cannot alter what B sees.
5. A rejected/stale-revision command leaves durable context unchanged.
6. Identical state + deterministic selection → identical canonical prompt assembly.
7. Undo restores effective visibility for the exact operation only when version conditions hold.
8. Every model-request manifest corresponds to one context revision and one pipeline configuration.

Use Hypothesis state machines to generate exclude/restore/update sequences. Keep formal TLA+ modeling optional until after feature freeze; it should not jeopardize voice, memory, weather, or deployment requirements. Present **bounded, specific invariants**, not claims that the model itself is formally verified.

---

## 17. Implementation schedule and release gates

The detailed, evidence-based [execution checklist](docs/execution_checklist_v5.md) is the authoritative progress tracker.

- **Day 1:** finish protected deployed English voice, weather, durable history, interruption/reconnect and cleanup acceptance. Worker deployment has been observed; hosted voice acceptance is still pending. Retain temporary OpenAI until DeepSeek funding confirmation.
- **Day 2:** atomic persistent memory, pgvector retrieval, authoritative context assembly, typed actions, text-based exclusions/Undo, five response tiers, Jev integration and safe provider failover.
- **Day 3:** Simple/Developer interface, orb startup, shared voice-controlled settings, memory explorer, spoken exclusions/restoration, actual request inspection, essential metrics, Arabic qualification. Freeze features at the end of the day.
- **Day 4:** regression tests, bug fixes, deployment/budget checks, measured latency and presentation rehearsal only. No new features.

Cut optional wake words, browser-local speech, extra providers, speculative partial routing, elaborate animation/statistics and implicit memory extraction before weakening voice, cross-conversation memory, weather, deployment or context-control requirements.

---

## 18. Tests and acceptance scenarios

### Acceptance scenario mapping

Detailed instructions, dependencies, pass/fail criteria, verification and evidence fields live in the [execution checklist](docs/execution_checklist_v5.md). A task is complete only when every criterion passes; code presence alone is insufficient.

| Scenario | Checklist tasks |
|---|---|
| Fresh scoped demo identity and protected reviewer access | M0.4, M0.12, D3.13, D4.5 |
| Spoken reply, interruption, reconnect, shared typed history | D1.3, D1.5, D1.6 |
| Cross-conversation preference recall | D2.4, D2.17 |
| Actual weather and included-only remembered location | D1.4, D2.8 |
| Semantic search, keep-budget preview, exact-ID confirmation | D2.7, D3.8, D3.9 |
| Revisioned diff, Undo and next-request exclusions | D2.5, D2.7, D2.17, V5 |
| Spoken command routing and shared GUI actions | D2.6, D2.15, D3.5, V3 |
| Mode toggle preserves conversation/audio | D3.7 |
| Internal provider capability checks and approved public settings | M0.6, D3.4 |
| Settings/context snapshots and actual outgoing requests | D2.5, D3.10 |
| Cross-owner/protected-record rejection, stale revision and duplicate commit | D2.3, D2.7, D4.4, V1, V5 |
| Provider failure, missing credentials and safe fallback | D2.11, D2.12, D2.14, V2, V4 |
| No provider secrets in browser bundles or evidence | M0.11, D3.4, V6 |
| Presentation and truthful limitations | P1, P2 |

These are mappings rather than duplicate progress checkboxes; checklist statuses are authoritative.

### The exact showcase demo

1. Open public URL in fresh browser; choose demo identity.
2. In Simple Mode, tell Sarjy: “Remember that I live in Riyadh. I'm planning a trip there in December. The budget is 3,000 SAR.”
3. Create another chat; ask: “What's the weather like where I live?” Observe the weather API call and real result.
4. Toggle Developer Mode. Search for *travel*, inspect records and source turns.
5. Say: “Forget everything about the Riyadh trip for this chat, except the budget.”
6. Display candidate selection and the preserved budget item; confirm.
7. Show before/after compiled context and revision 7→8; ask something that would normally use the removed travel facts.
8. Verify no selected/excluded IDs appear in next compiled request; then press Undo and inspect restored context.
9. Show read-only routing/provider diagnostics and a recorded controlled failover test; provider selection remains internal.
10. Open code and show: pure transform, Hypothesis property test, provider factory, and weather tool authorization.

Keep one-minute backup clips of key interactions if network/provider demos fail. Never make prerecorded playback look like a live result.

---

## 19. Repository layout and implementation conventions

```text
sarjy/
├── apps/
│   ├── web/
│   │   ├── src/routes/chat/[id]/+page.svelte
│   │   └── src/
│   │       ├── components/chat/
│   │       ├── components/context-explorer/
│   │       ├── components/developer-panel/
│   │       ├── audio/livekit-runtime.ts
│   │       ├── audio/browser-local-runtime.ts      # optional/experimental
│   │       ├── audio/voice-runtime.ts
│   │       ├── stores/
│   │       └── api/
│   └── api/
│       └── app/
│           ├── main.py
│           ├── api/{sessions,conversations,context,pipelines,weather}.py
│           ├── domain/{commands,context,models,transformations}.py
│           ├── services/{memory,context_compiler,weather}.py
│           ├── providers/{registry,embeddings,llm}.py
│           ├── infrastructure/{database,auth}.py
│           └── config.py
├── services/
│   └── voice-agent/
│       ├── main.py
│       ├── provider_factory.py
│       ├── config.py
│       └── events.py
├── packages/
│   └── contracts/                            # OpenAPI types / JSON event schemas
├── migrations/
├── tests/
│   ├── property/
│   ├── integration/
│   └── browser/
├── docs/
│   ├── architecture.md
│   ├── context-semantics.md
│   ├── providers-and-capabilities.md
│   ├── benchmarking.md
│   ├── security-and-privacy.md
│   ├── decisions.md
│   └── limitations.md
├── infra/
│   ├── api.Dockerfile
│   ├── agent.Dockerfile
│   └── docker-compose.yml
├── .env.example
├── README.md
├── pnpm-lock.yaml
└── uv.lock
```

**Do not over-abstract for the sake of symmetry.** Use one native vendor plugin adapter per actually enabled remote model, one capability registry, one context domain, and one browser runtime implementation until there is demonstrable need for another. Avoid constructing a bespoke replacement for LiveKit's native audio streaming APIs.

---

## 20. Interview narrative and architectural decisions

Make the reviewers able to follow these claims **in working code**:

1. **User experience:** Simple Mode looks and feels like a modern voice chatbot; Developer Mode exposes powerful controls without disrupting the call.
2. **Novel interaction:** A natural-language instruction can *exclude* prior context, while preserving a named exception, and provides a visible, reversible diff.
3. **AI vs deterministic logic:** The model interprets intent and suggests IDs; pure functions and database constraints decide what can actually change.
4. **Persistent memory:** History is not memory. Important facts survive independent sessions; only selected facts enter a given model request.
5. **Honest model visibility:** Differentiate canonical compiled messages from the exact provider wire request when the SDK doesn't expose all provider transformations.
6. **Provider modularity:** One validated typed configuration composes LiveKit-compatible provider instances; browser-local runs in a separate client runtime because the execution topology is different.
7. **Testing:** Property-based laws cover exclusion behavior, protected context, tenant isolation and undo; integration tests cover real WebRTC and weather.
8. **Trade-offs:** You optimized **scope and correctness** rather than self-hosting Kubernetes, inventing a universal inference protocol, or adding many untested models.
9. **Availability:** Reviewers get a public URL and a working demo, not a README that requires installing the entire stack.
10. **Latency truthfulness:** On-device speech is an experiment; measure actual audio latency rather than assuming local inference is better.

---

## 21. Official references and verified constraints (checked 11 Nov 2026)

### Models and pricing

- [OpenAI current API models](https://developers.openai.com/api/docs/models) — GPT-6 Luna, GPT-6.1 Terra, GPT-6.1 Sol IDs and capabilities.
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) — verify actual model and service-tier prices immediately before paying.
- [DeepSeek V4.1 Flash announcement](https://api-docs.deepseek.com/news/news260910/) — direct identifier `deepseek-flash`.
- [DeepSeek model in LiveKit](https://docs.livekit.io/agents/models/llm/deepseek/) — managed Inference identifier and supported integration.
- [LiveKit pricing](https://livekit.com/pricing) — Build/Ship plan limits and inference pricing.
- [LiveKit Inference](https://docs.livekit.io/agents/models/inference/) — gateway and model availability.
- [LiveKit OpenAI LLM plugin](https://docs.livekit.io/agents/models/llm/openai/) — Responses API usage.

### Speech models and provider adapters

- [Deepgram pricing and starter credits](https://deepgram.com/pricing) — $200 initial credits, direct STT and TTS.
- [LiveKit Deepgram STT](https://docs.livekit.io/agents/models/stt/deepgram/) — Arabic Nova-3 support and streaming integration.
- [LiveKit Deepgram TTS](https://docs.livekit.io/agents/models/tts/deepgram/) — model-dependent TTS support.
- [Deepgram Flux TTS voice catalog](https://developers.deepgram.com/docs/flux-tts/voices) — **source of truth for Flux voice IDs**, with no Arabic voice listed as of 11 November 2026.
- [Deepgram October 2026 Flux TTS expansion](https://developers.deepgram.com/changelog/2026/10/23) — 41-language expansion excludes Arabic; recheck if catalog changes.
- [ElevenLabs pricing](https://elevenlabs.io/pricing) — 10k monthly free credits (plan restrictions may apply).
- [ElevenLabs supported models/languages](https://elevenlabs.io/docs/overview/models) — **Arabic in `eleven_flash_v2_5` and `eleven_multilingual_v2`**; Flash v2 (without `.5`) is English-only.
- [ElevenLabs supported-language documentation](https://elevenlabs.io/docs/help-center/other/what-languages-do-you-support) — Arabic listed for multilingual v2 and Flash v2.5.
- [LiveKit ElevenLabs STT plugin](https://docs.livekit.io/agents/models/stt/elevenlabs/) — direct API account required; retired from managed Inference.
- [LiveKit ElevenLabs TTS plugin](https://docs.livekit.io/agents/models/tts/elevenlabs/) — same retirement warning, direct plugin remains supported.
- [Nabra browser-local Arabic TTS](https://github.com/Oddadmix/nabra-tts) — experimental local option.

### Hosting and deployment

- [LiveKit agent deployment quickstart](https://docs.livekit.io/deploy/agents/quickstart/).
- [CranL official site and pricing](https://cranl.com/) — Free offers two app/database slots; Basic $4.99, Pro $9.99.
- [CranL deployment quickstart](https://docs.cranl.com/getting-started/quickstart.html) and [plan limits](https://docs.cranl.com/getting-started/sign-up.html).
- [Google Cloud Run free allowances](https://cloud.google.com/run/pricing) and [FastAPI quickstart](https://docs.cloud.google.com/run/docs/quickstarts/build-and-deploy/deploy-python-fastapi-service).
- [Koyeb Free instance limitations](https://www.koyeb.com/docs/reference/instances).
- [Render Free spin-down behavior](https://render.com/docs/free).
- [Vercel Hobby restrictions](https://vercel.com/docs/plans/hobby).
- [Supabase free database limitations](https://supabase.com/pricing).
- [Open-Meteo](https://open-meteo.com/) and [Open-Meteo API docs](https://open-meteo.com/en/docs) — verify noncommercial terms and attribution.

**Research limitations:** Plan eligibility, API model access, latency, local model performance, free credit availability, and hosting startup times are account/device dependent. CranL is the confirmed hosting platform; verify your account limits, SvelteKit deployment compatibility, and uptime during the Day 1 smoke test. The document deliberately does not promise an unsupported provider-language combination or claim a cost benchmark that was not actually measured.

---

## Final decision

**Build the application first; make it extensible without implementing every optional adapter.** The safest day-three MVP is a deployed **Sarjy voice chat with weather, cross-session memory, spoken context exclusion and Undo, and a compact Developer Mode**. The provider registry should offer DeepSeek and verified OpenAI alternatives; the **default Arabic speech path is Deepgram Nova-3 STT + ElevenLabs Flash v2.5 TTS**, with other providers enabled only after language/voice compatibility tests. Browser-local STT/TTS stays an honest optional experiment with a clean client runtime interface.

The presentation's signature moment is **not** changing API providers: it is speaking a context-edit command, seeing the exact proposed change, confirming it, and watching the next model request omit the excluded information—with a traceable before/after diff and an Undo action.


## Implementation amendment — SvelteKit, LiveKit Build, and testing

This amendment takes precedence over schematic framework examples above.

- Frontend: SvelteKit + TypeScript + Tailwind, official Node adapter, pnpm. Use Svelte reactive state; do not introduce React dependencies. Keep browser media in a framework-independent `LiveKitVoiceRuntime` backed by `livekit-client`.
- First integration gate: prove Arabic input, audible output, typed input, interruption, blocked autoplay handling, and reconnect before expanding UI. Own the runtime in the persistent chat layout; clean up tracks, audio elements, and listeners. Language switching preserves the call; conversation switching explicitly ends it.
- Host the frontend and FastAPI on CranL, the worker on LiveKit Build, and PostgreSQL on Supabase. Verify account allowances: Build advertises 1,000 agent minutes/month, five concurrent agents, 5,000 WebRTC participant minutes, 50 GB transfer, and $2.50 inference credits. Direct provider usage has separate billing. Free quotas are hard caps; agents may cold-start in 10–20 seconds.
- Keep the direct Arabic preset primary. Limit optional managed DeepSeek/Cartesia tests to 30 minutes and $0.50 inference credit; enable only tested presets. Reserve 200 agent minutes for reviewer use.
- Same-origin `/api` proxy forwards browser cookies and backend Set-Cookie headers. FastAPI remains authoritative for ownership and state. Validate production origin configuration for the installed SvelteKit version.
- Demo limits: one active call per identity, two globally, ten minutes per call; three reservations per demo identity and 80 reservations per calendar month by default (conservative maximum 800 agent minutes, separate from other account usage). Shut down the worker after 60 seconds without its browser participant.
- Tests should use functional programming style whenever possible: immutable inputs, pure transformations, property-based invariants, deterministic state transitions. Test side effects with focused database/API/LiveKit integration tests; avoid implementation-mirroring mocks. Use svelte-check, production build, and browser acceptance checks.
- Day 1 order: verify services → scaffold/deploy skeleton → prove Svelte voice loop → identity/persistence/context compiler → weather/bilingual UI → public demo verification. Cross-session extracted memory and context exclusions remain Day 2.

### Voice selection amendment

The TTS voice is a user-selected setting rather than `ELEVEN_VOICE_ID` deployment configuration. Provide a searchable, paginated picker backed by the server-side ElevenLabs account voice catalog. Retain selection in the browser; send it explicitly when starting each call, revalidate availability server-side, and snapshot it with the immutable call pipeline. Changes apply to the next voice session without reconnecting the current call. Keep API keys server-side; do not silently substitute unavailable voices or claim Arabic quality from a language label. Exclude custom-rate voices from this bounded demo catalog.

### Follow-up: conversational voice personalization

The picker is the initial interface. The intended follow-up lets users say, for example, “I want to speak to a British young lady.” An LLM interprets the desired language/accent, perceived gender, age category, and vocal qualities into structured search criteria; deterministic code resolves those criteria against actual accessible catalog voices and applies the selected ID. Never invent a voice ID or use keyword rules to interpret the request. If no close match exists, offer alternatives or ask for the most important characteristic. Provider labels are descriptive metadata, not verified identity or quality guarantees.

For in-call spoken changes, finish/cancel current playback as appropriate and apply the new voice at a safe response boundary. Version the effective pipeline selection and capture the actual voice in subsequent turn snapshots; do not mutate old snapshots or restart the conversation. Live voice switching must be verified against the installed TTS plugin before enabling it. This conversational flow is follow-up work; the initial picker continues to apply changes on the next voice session.

### Development provider and budget override

User instruction: use OpenAI temporarily; switch DeepSeek back to default only when the user confirms funding. Use `gpt-6-luna` through the Responses API with reasoning disabled and bounded output. The **$1/day limit applies specifically to OpenAI**, not all vendors combined. Enforce shared, persistent, atomic development reservations before inference; reserve $0.02/request conservatively, including tool continuations, and never refund uncertain/failed requests. Guard the bounded text-only context and disable hidden provider/SDK retries. Daily rollover is Africa/Tripoli midnight. Verify prices before any model or request-bound changes.

For ElevenLabs, be more conservative: initially allow at most 500 synthesized characters/day through the same ledger with its own bucket. Prefer text tests, do not run unattended audio simulations or synthesis loops, and do not purchase credits or silently switch providers. Reserve characters before forwarding text to the native streaming plugin. Display budget-exhaustion errors honestly. Guards cover Sarjy development calls; unrelated uses of the same API credentials are outside application enforcement.

## 22. Version 5 decision layer and shared action architecture

### Motivation and boundaries

Jev avoids conversational generation for application controls and selects an appropriate verbal budget for other requests. It is neither the conversational assistant, memory database, nor executor. Adding a remote classifier can increase latency: retain an exact local-command fast path and a complete bypass path.

Verified 11 November 2026: [TypeSafe API](https://docs.typesafe.ai/api/system-one) accepts `POST https://api.typesafe.ai/v1/systemone`, bearer authentication, `state`, `model`, and named `questions`. Pin `jev-1.14.0`. Choice returns an option, probabilities and confidence; Score returns a rubric position and distribution; Noul returns a truth probability. Ask three independent Choice questions in one request: intent, response tier and complexity. Jev cannot generate arbitrary argument strings. [Pricing](https://help.typesafe.ai/articles/5187202169-how-much-does-jev-cost) is $0.042/M input tokens, output free; signups are open without trial credits. Account access and deployed latency remain unverified.

### Processing flow

```text
Deepgram finalized transcript / typed message
 -> shared FastAPI turn ingress
 -> exact local command, otherwise optional Jev
 -> validate decision and resolve typed arguments
 -> authorized action handler OR authoritative context assembly
 -> DeepSeek streaming, thinking disabled
 -> eligible early failure: budgeted OpenAI fallback
 -> budgeted ElevenLabs speech
```

Cache routing by turn/request ID; do not reclassify tool continuations. Jev receives bounded permitted context, never the full memory store. Intent categories are conversation, ui_control, voice_setting, memory_search, memory_modification, conversation_navigation, tool_request and ambiguous. Complexity is simple/complex, independent of verbosity; it can guide retrieval, clarification and observability, never reasoning escalation.

Immutable decision outputs include category, tier, complexity, distributions, source/model, turn ID and policy version. Reject malformed/unknown outputs. Initially require selected-intent probability >=0.90 and a >=0.20 lead for automatic control routing. These are provisional application thresholds, not correctness guarantees. Vendor confidence summarizes its distribution, not independent evidence. Ambiguous/compound commands require clarification; quoted or negated instructions must not execute.

### Shared typed actions

UI and speech dispatch identical actions: AdjustSpeakingSpeed, SetSpeakingSpeed, SelectApprovedVoice, ShowConversationHistory, OpenMemoryExplorer, SetCaptionsVisible, SearchMemory, ProposeContextExclusion and RestoreContextOperation. Presentation actions use one frontend reducer; durable actions use backend-authorized handlers. Neither model can execute arbitrary JavaScript, write database records directly, bypass ownership, or confirm its own context proposal.

Use deterministic extraction for closed-set commands. Use a bounded schema-validated LLM extraction request for free-form memory topics, keep-exceptions and ambiguous references. Search approved account voices only; report no match honestly. Default speed 1.0, initial supported range 0.8–1.2 and relative step 0.1; this conservatively matches the pinned plugin comment despite the vendor's broader documented range. Apply speed/voice changes at the next synthesis boundary, update controls, and show pending/applied state. Fixed acknowledgments need no conversational LLM; optional speech still consumes the character allowance.

### Response policy

| Tier | Maximum output tokens | Use |
|---|---:|---|
| 1 | 64 | Short answer or acknowledgment |
| 2 | 128 | Normal conversation |
| 3 | 256 | Moderate explanation |
| 4 | 512 | Detailed explanation |
| 5 | 1024 | Explicitly detailed response |

Caps are configurable ceilings, never target lengths. Public preferences: Automatic (default), Brief (maximum tier 2), Balanced (maximum tier 3), Detailed (all tiers). Explicit current-turn length instructions override saved preferences within server bounds. Bypass uses deterministic length cues, otherwise tier 2. Difficult mathematics with a one-sentence answer can be complex/tier 1; a long creative story can be simple/tier 5. Update the existing 800-token guard and budget tests before enabling 1024.

### Jev availability and evaluation

Reuse an asynchronous client, disable retries, and impose a 750 ms total decision deadline. Bypass on timeout, invalid output, missing key, exhausted allowance or provider failure. Three consecutive operational failures open a five-minute bypass window. Speculative partial-transcript classification is research only: never act, speak or change state before finalization.

Use 24 labeled text cases across eight intents, five tiers, ambiguity, negation and compound instructions. Separate tuning and holdout cases. Compare local, Jev-assisted and direct-primary routing: intent/tier agreement, false action routing, requests avoided, costs and end-to-end latency. Initial automatic-routing gate: >=90% holdout intent agreement, no unsafe executions, <=250 ms median added conversational overhead. Publish raw observations/sample size, not production reliability claims. Account key setup precedes paid checks.

## 23. Provider-independent generation and failover

An immutable model request carries system instructions, permitted history/memories/provenance, context revision, included/excluded IDs, supported tools/completed results, response policy, settings revision, turn ID and attempt ID. Adapters normalize streaming text, tools, usage, errors and terminal status. Rebuild from authoritative state; do not depend on opaque provider conversation state.

DeepSeek's [documented Chat Completions setting](https://api-docs.deepseek.com/guides/thinking_mode/) is `extra_body={"thinking":{"type":"disabled"}}`, with `stream=True` and model `deepseek-flash`. Thinking is enabled by default, so the override is mandatory. Never invent an `instant` field or dynamically raise reasoning effort. OpenAI fallback uses `gpt-6-luna`, Responses API, reasoning none, store false, guarded requests and no hidden retries.

Permit one primary attempt and at most one fallback for connection failure, timeout, transient rate limiting or server overload before any text/tool-call output in the turn. Do not fall back for invalid configuration, authentication, insufficient balance, application quota, cancellation, or emitted text/tool calls. Preserve partial response status and executed/uncertain tool outcomes; never replay side effects blindly. Stable operation IDs and pending/completed/failed/unknown ledger states replace fresh-per-invocation IDs. LiveKit's native fallback error eligibility is broader than this policy, requiring an application guard.

Initial configurable deadlines: first substantive output 5 seconds, inter-chunk inactivity 8 seconds, overall response attempt 30 seconds. Tools have bounded deadlines within a bounded turn budget. Reserve before every OpenAI attempt; if denied, return an explicit unavailable status. No provider loops or difficulty-based escalation.

## 24. Version 5 interface, memory and operational specification

Use SvelteKit/Svelte 5/TypeScript/Tailwind. Left: conversations; center: interactive orb; right: collapsible memory/settings. Orb states: idle, initializing, listening, processing, speaking, error; keyboard support, reduced motion, captions and accessible microphone controls are mandatory. Simple/Developer switching preserves the live session. Public controls expose speed, approved voice, language, response-length preference, captions and context only. Developer diagnostics may show read-only providers/usage, never selection, credentials, reasoning or paid inference settings.

Add an authorized typed action endpoint, settings read/update, semantic memory search, context proposal/commit/Undo/history and owner-scoped snapshot/metrics endpoints. Regenerate contracts; write operations use idempotency and expected revisions. Migrations add atomic memories with source/version and vector(1536), settings revisions, context proposals/exclusions/operations, immutable snapshots and execution metrics. Enable pgvector; use OpenAI text-embedding-3-small behind a replaceable adapter, owner-filtered exact cosine retrieval initially. Explicit memory requests precede implicit extraction. Corrections create source-aware versions.

Exclusion defaults to the current conversation and requires a server-owned exact-ID preview and confirmation. Keep-exceptions survive atomic commit. Filter excluded IDs at final assembly, including tracked source/derived records; avoid long-term generated summaries initially. Undo requires operation identity and expected revision; stale edits fail without overwriting later changes. Exclusion does not prove semantic erasure of independent records, previous provider inputs or retention. Permanent deletion is deferred and must never be falsely acknowledged.

Capture canonical context/hash separately from the actual serialized outgoing inference payload, without transport secrets. Capture tool continuations and fallback attempts; label unavailable wire capture honestly. Context changes affect the next turn by default, with an explicit indication during active generation.

Retain the shared OpenAI $1/day ceiling for all application/development requests including embeddings and extraction, initially reserving $0.02 each. ElevenLabs remains 500 characters/day. Proposed Jev cap: $0.10/day with conservative $0.003/request reservations against its documented standard context maximum; revalidate on price/context changes. All guards share the durable backend ledger and Africa/Tripoli day boundary; no refunds after uncertain delivery, counter resets, automatic purchases or unattended audio loops. Longer tiers never raise speech quotas: show text with an explicit speech-limit state when necessary. Track actual vendor usage separately from reservations.

Keep CranL web/API, LiveKit eu-central and Supabase London. If CranL blocks progress, use a supported SvelteKit host (Vercel subject to eligibility) and Cloud Run backend container, retaining contracts/database/ledger. Development stays password-protected; reviewer access uses HTTPS and simple credentials without opening anonymous inference. Inspect ignored files and secret scans before releases, preserve root Dockerfile branch overlays, and deploy only CI-tested revisions. Optional local “Hello, Sarjy” wake detection must never stream idle audio to paid models; defer wake words and browser-local inference if mandatory work is at risk.

Presentation: start orb voice chat; teach atomic city/trip/budget facts; recall city in another conversation and fetch weather; speak a speed change and show slider synchronization; inspect provenance; exclude trip except budget; confirm, show revision/diff and actual outgoing input; restore; show controlled failover evidence, property tests and budgets. Label backup recordings explicitly.


## Day 2 execution update — 12 November 2026

User authorized local Day 2 work while issue #3 human acceptance remains pending. PRs #23 and #24 are merged; D2.1, D2.2 and D2.9 have local implementation/verification evidence. No hosted Day 2 migration or deployment has occurred. Issues #5 and #7 consume the merged foundation; #8 consumes their interfaces; #6 completes context/weather integration afterward. Issue #4 closes after cross-cutting isolation, budget and property verification across those paths. Noon Africa/Tripoli is the target, not permission to omit acceptance criteria. See docs/day2-coordination.md and issue #20.
