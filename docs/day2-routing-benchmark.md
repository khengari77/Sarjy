# Day 2 routing benchmark

Status: **offline implementation**. No live Jev or DeepSeek benchmark has been
run. Automatic routing remains disabled.

This document describes the frozen 24-case corpus, the resumable harness and the
holdout gate for issues #12/#13. The harness is prepared for Codex to execute the
explicit live run inside the shared budget.

## Corpus and split

`tests/fixtures/day2_routing_cases.json` contains exactly 24 English cases:

- 8 tuning cases;
- 16 holdout cases;
- all eight intents: `conversation`, `ui_control`, `voice_setting`,
  `memory_search`, `memory_modification`, `conversation_navigation`,
  `tool_request`, `ambiguous`;
- all five tiers;
- ambiguity, negation, quotation and compound instructions.

Labels describe intended behavior only: intent, tier, complexity, the expected
action kind (or none), the expected effect/arguments and whether clarification is
expected. They were authored before any model output existed. No model-generated
answer is ground truth.

The holdout cases and their digest were frozen before tuning. The digest covers
the dataclass-normalized holdout cases (`id`, `split`, `text`, expected fields,
expected effect, tags and rationale) in fixture order, using canonical JSON
(`sort_keys=True`, compact separators). Fixture version 2 is a post-review label
correction that added expected effects before any live or scripted model output;
the holdout digest was refrozen for that correction. `validate_fixture` rejects
any later change to the holdout split. Do not tune on holdout outputs and continue
describing them as unseen.

Expected effects are scored, not just action kinds. Settings actions compare the
resulting settings fields, navigation actions compare the navigation target,
memory searches require the labeled query terms, and exclusion proposals require
the labeled proposal terms. An authorized action with the right kind but wrong
arguments or effects is an unsafe authorization and fails the gate.

## What is compared

`scripts/day2_routing_benchmark.py` compares:

1. **local/exact** — the existing whole-utterance fast path only;
2. **Jev-assisted** — exact path first, otherwise one Jev classification and at
   most one bounded structured-extraction call when the decision is eligible for
   an automatic control action;
3. **direct** — an injected primary-classification/extraction client.

Each proposed action is applied to a fresh `IsolatedActionState`. Settings
changes are pure local transforms; memory reads, exclusion previews and Undo are
mocked and recorded. No database, user data or external effect is touched.

## Execution modes

```sh
# Default: validate the fixture and report structure; no provider calls.
uv run python scripts/day2_routing_benchmark.py --dry-run

# Replay recorded responses through the real validators.
uv run python scripts/day2_routing_benchmark.py --scripted path/to/script.json

# Explicit live execution. Requires an operator-supplied module.
uv run python scripts/day2_routing_benchmark.py --live --live-module path/to/clients.py
```

Dry run is the default when neither `--scripted` nor `--live` is supplied.
`--live` without `--live-module` exits with an error; no unreviewed live adapter
or spending ledger is built into the script.

The live module must define:

```python
def build_routers(*, fixture, cache):
    """Return routers with ids local_exact, jev_assisted and direct."""
```

Each router exposes `id`, `model`, `prompt_hash(case)` and
`async route(case) -> RouterOutput`. The module owns Jev/DeepSeek clients,
credentials and reservation callbacks. It must not write a second spending
ledger.

### Scripted format

```json
{
  "responses": {
    "tune-02": {
      "classification": {
        "model": "jev-1.14.0",
        "answers": { "...": "..." },
        "usage": {"input_tokens": 50, "output_tokens": 5}
      },
      "extraction": {"kind": "AdjustSpeakingSpeed", "delta": -0.1},
      "direct": {
        "intent": "voice_setting",
        "tier": 1,
        "complexity": "simple",
        "action": {"kind": "AdjustSpeakingSpeed", "delta": -0.1},
        "usage": {"input_tokens": 40, "output_tokens": 4}
      }
    }
  }
}
```

Scripted Jev responses go through `JevClient` and the domain validator, not a
shortcut. Scripted extraction goes through `extract_action`, including the
quotation/negation/compound safety guard. A missing entry becomes a preserved
failure, never a dropped case.

## Cache and resume

Results are cached by a SHA-256 of execution mode, case ID, router ID, model,
router provenance and prompt hash. Scripted and live runs use separate caches:

- `artifacts/day2-routing-benchmark/cache-scripted.json`
- `artifacts/day2-routing-benchmark/cache-live.json`

An explicit `--cache` path is still mode-checked; a live run refuses a scripted
cache. Each entry stores and validates its cache key, mode, case, router, model,
prompt hash and provenance. A resume reads the cache and does not repeat work.
Failures are cached too, so a rerun does not silently retry a paid failure to
improve the result. Scripted evidence can never satisfy the live gate.

## Recorded metrics

Per router the report includes:

- intent, tier and complexity agreement;
- action proposals, unsafe proposals and unsafe authorizations;
- invalid action proposals;
- requests issued and requests avoided compared with one direct call per case;
- raw latency samples and median latency;
- per-model usage records (`provider`, `model`, `purpose`, input/output tokens);
- per-model estimated cost and an overall total only when every used model is
  explicitly priced;
- failures and sample size.

The holdout gate is:

- the run mode and every Jev/direct result must be live, never scripted;
- at least 90% intent agreement on 16 holdout cases;
- zero unsafe authorizations, including wrong arguments/effects;
- median paired added Jev overhead at most 250 ms;
- all 16 Jev results and all 16 direct results measured without errors, with all
  16 valid latency pairs.

A failed gate is a valid result. The report sets
`automatic_routing_enabled: false` unconditionally; passing the gate is evidence
for review, not an automatic change to production routing.

## Interfaces

- `app.domain.routing.build_classification_state` bounds instruction and context
  and never truncates the current instruction.
- `app.domain.routing.validate_classification_payload` validates the three
  independent Choice answers and returns a `Decision` or safe bypass.
- `app.services.jev.JevClient(api_key, reserve, transport=..., clock=...)`
  requires an injected reservation callback and enforces the 750 ms deadline,
  zero retries and the circuit breaker.
- `app.services.action_extraction.extract_action(...)` requires injected
  reservation and generation callbacks plus an explicit `GenerationConfig`
  (`model`, bounded `max_output_tokens`, input/output prices). The reservation
  covers worst-case input plus bounded output tokens, and the generation callback
  receives a typed `GenerationRequest`. It returns a typed action or
  clarification and never executes or mutates state.

## Limitations

- No live Jev key/account check, no live DeepSeek benchmark, no measured deployed
  latency and no production cost measurement.
- Scripted results validate the harness, not model quality, and always fail the
  live gate with `NOT_LIVE`.
- Direct-model pricing is only recorded when explicitly supplied.
- The corpus is English text only; Arabic remains out of scope.
- No automatic-routing switch was implemented or enabled.
- The extraction safety guard is intentionally conservative: quoted, negated or compound phrasing is clarified even when a human might read a supported compound.
