# Day 2 ownership and interfaces

Target: noon Africa/Tripoli, 12 November 2026. #3 does not block local implementation. No hosted or paid operations.

| Owner | Scope |
|---|---|
| Codex day2_boundaries | #4 shared authorization/budget helpers, migration coordination, isolation properties |
| Codex day2_memory | #5 repository/service, guarded embeddings, correction versions, semantic retrieval |
| Codex day2_dispatcher | #7 exact parser, authorized dispatcher, settings handlers |
| OpenCode worker A | #6 authoritative compiler/reader, worker context, weather permissions |
| OpenCode worker B | #8 proposals/confirmation/exclusions/diffs/Undo |
| OpenCode worker C | independent integration/property/PostgreSQL verification |

Publish exact service signatures in issue #20 before consumers integrate. Existing immutable contracts are authoritative. Memory operations carry owner/conversation/source, idempotency and expected version for correction. Search returns immutable records with provenance/version. Dispatcher adapters share validated actions; context handlers delegate to proposal services. Proposals capture exact IDs, keep exceptions and base revision; confirmation is authorized and atomic. Context reader supplies one revision and exclusions, filtering tracked sources before compilation. Coordinator owns generated contracts; boundaries agent coordinates ordered additive migrations. No historical migration edits.

Use separate disk worktrees, local databases and dependency outputs. Serialize browser tests using the shared lock. Agents open PRs and never merge, deploy or close issues. Coordinator reviews and merges prerequisites before integration. Keep #4 open until downstream assurance is complete; reconcile #9 from merged evidence. Deadline never overrides criteria. #27 remains parked; #29 is separate.


## Issue #10 provider integration — 12 November 2026

- Branch `fix/day2-provider-adapters`; isolated worktree `artifacts/worktrees/day2-provider-adapters`.
- Shared request snapshots, native OpenAI Responses/DeepSeek adapters and normalized streams implemented; generated contracts updated.
- DeepSeek funding and one bounded live check verified; DeepSeek becomes default for new sessions. Explicit OpenAI configuration remains available. No hosted rollout.
- #11 remains responsible for automatic fallback and durable tool identities. Existing generic tool callback request IDs are unchanged by this work.
- Final PR/CI carries acceptance evidence; do not infer deployment or hosted voice acceptance from these results.
