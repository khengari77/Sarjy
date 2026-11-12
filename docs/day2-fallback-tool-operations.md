# Fallback and durable tool operations — issue #11

Status: implementation draft. No live fallback request, hosted acceptance or
merge is claimed.

## Fallback policy

`services/voice-agent/fallback.py` owns one pure decision:

- fallback is eligible only before any text/tool output;
- eligible failures are timeout, connection failure, rate limiting and transient
  5xx provider errors;
- permanent HTTP statuses (400/401/402/403/404/405/409/410/422 and similar)
  always deny fallback before generic error categories are considered;
- cancellation, authentication, invalid request, insufficient balance and quota
  denial never fall back;
- at most one fallback attempt is made;
- the fallback request receives a new attempt identity but keeps the same turn,
  snapshot, tools, instructions and response policy.

The worker reserves OpenAI through the existing durable budget before the
fallback attempt. A denied reservation forwards nothing to the fallback provider.
Startup passes the optional OpenAI key to the provider factory even when the
primary is DeepSeek; the key remains optional and primary-only startup still
works without it.

## Tool operation identities

`/internal/agent/tool/weather` accepts the worker’s stable operation key, derived
from the model tool-call identity. The backend claims the key before calling the
weather service:

- `pending` — another delivery is in progress; no second external call;
- `completed` — return the stored result;
- `failed` — return the stored terminal error;
- `unknown` — return uncertainty and never replay the effect.

The operation key is bound to the tool name and a canonical fingerprint of its
arguments. Reusing one key with different arguments or a different tool returns a
typed conflict instead of another operation’s result.

Only completed tool results enter model context. A timeout or unrecognized
delivery is recorded as `unknown`; it is not silently retried. A different
operation key remains a distinct operation.

## Data and migration

`tool_calls` gains `state` and `error_code`; `result` becomes nullable while a
claim is pending. Migration `0008_tool_operation_state` is additive and
backfills legacy rows as completed. The table already belongs to the private
backend grant/RLS set.

## Verification

- focused fallback policy and worker orchestration tests;
- tool-operation state/replay tests on SQLite;
- API replay, unknown-delivery and cross-turn conflict tests;
- full backend suite;
- no paid provider requests.

PostgreSQL migration/concurrency coverage remains required before final review if
the coordinator’s disposable database is available.

## Limitations

- The fallback live path is not exercised against a real provider in this draft.
- Pending claims have no automated lease recovery; a process death leaves a
  typed in-progress outcome rather than replaying an uncertain effect.
- Hosted deployment and human acceptance are out of scope.
