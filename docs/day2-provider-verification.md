# Day 2 provider adapters — issue #10

Verified 12 November 2026. No hosted deployment, migration or speech synthesis.

## Result

The API persists an immutable `ModelRequest` in each authoritative turn snapshot. It carries full ordered canonical messages, permitted memory provenance/version, context and settings revisions, response policy and turn identity. The worker gives each inference attempt a new identity, checks snapshot consistency, and attaches typed tool calls/results without restoring stale conversation history.

OpenAI Responses and DeepSeek Chat Completions use their native LiveKit plugins behind one streaming boundary. It normalizes text, tool calls, usage, completion, errors and cancellation. Provider failures preserve whether output already escaped. SDK and stream retries are disabled. Tool execution and automatic fallback remain outside adapters; #11 owns fallback and durable tool-operation policy.

The adapters use the request's exact tier ceiling. OpenAI retains reasoning none, store false and reservation-before-send; DeepSeek explicitly disables thinking. OpenAI remains an explicit configuration option. New sessions default to DeepSeek after the funded check below; sessions already started retain their stored pipeline.

Shared domain contracts are included in the worker image. Public routes are unchanged; generated schemas include the new message/continuation contracts. Memories without source turns retain `null` provenance rather than inventing a source.

## Offline evidence

- Real SDKs with scripted HTTP streaming transports: both provider payloads, full context, all five caps, fragmented tool arguments, completed tool-result continuation, usage, malformed streams, 503/no retries, partial failure and cancellation/closure.
- Worker boundary: stale history replaced; OpenAI reservation precedes inference; denial sends no provider request.
- New sessions: DeepSeek pipeline persisted and returned at bootstrap; missing DeepSeek key does not silently choose another provider. Existing explicit OpenAI lifecycle tests remain covered.
- Frontend: check (zero errors/warnings) and build passed.
- Worker image: `docker build -f infra/agent.Dockerfile -t sarjy-agent:issue10 .` passed; shared-contract/worker imports passed in the image with networking disabled.
- Final backend and PostgreSQL/browser results are attached to the PR's CI checks.

## Funded live evidence

User confirmed funding and explicitly approved the bounded check. The production factory and adapter made one text-only streaming request, with thinking disabled, at most 64 output tokens and zero retries:

- UTC: `2026-11-12T15:36:44Z`
- Model: `deepseek-flash`
- Result: `OK`; normal completion in 1.695 seconds
- Observed usage: 12 input tokens, 1 output token, 0 reasoning tokens
- Conservative peak-price estimate: $0.0000048; this is an estimate from observed tokens, not a billing statement.
- User's subsequent total verification cap: $0.25. No additional paid calls were sent. Optional live tool experiments were rejected by automatic approval review; the tool path is verified with scripted HTTP streams.

Raw non-secret evidence is retained locally at `artifacts/issue10/live-verification.json` (ignored). Credentials were never included in evidence or source.

References: [DeepSeek thinking controls](https://api-docs.deepseek.com/guides/thinking_mode/), [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/), [LiveKit OpenAI/compatible provider plugins](https://docs.livekit.io/agents/models/llm/openai/).
