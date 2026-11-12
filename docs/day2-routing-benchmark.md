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
action kind (or none) and whether clarification is expected. They were authored
before any model output existed. No model-generated answer is ground truth.

The holdout cases and their digest were frozen before tuning. The digest covers
the dataclass-normalized holdout cases (`id`, `split`, `text`, expected fields,
tags and rationale) in fixture order, using canonical JSON (`sort_keys=True`,
compact separators). `validate_fixture` rejects any change to the holdout split.
Do not tune on holdout outputs and continue describing them as unseen.

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

Results are cached by a SHA-256 of case ID, router ID, model and prompt hash.
The default cache is `artifacts/day2-routing-benchmark/cache.json` (ignored).
Each cache entry retains the result, usage, latency, raw non-secret request/
response metadata where supplied, and any failure. A resume reads the cache and
does not repeat work. Failures are cached too, so a rerun does not silently
retry a paid failure to improve the result.

## Recorded metrics

Per router the report includes:

- intent, tier and complexity agreement;
- action proposals, unsafe proposals and unsafe authorizations;
- invalid action proposals;
- requests issued and requests avoided compared with one direct call per case;
- raw latency samples and median latency;
- input/output tokens and estimated cost where a price was supplied;
- failures and sample size.

The holdout gate is:

- at least 90% intent agreement on 16 holdout cases;
- zero unsafe authorizations;
- median paired added Jev overhead at most 250 ms;
- all 16 holdout cases measured and paired latency available.

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
  reservation and generation callbacks, returns a typed action or clarification,
  and never executes or mutates state.

## Limitations

- No live Jev key/account check, no live DeepSeek benchmark, no measured deployed
  latency and no production cost measurement.
- Scripted results validate the harness, not model quality.
- Direct-model pricing is only recorded when explicitly supplied.
- The corpus is English text only; Arabic remains out of scope.
- No automatic-routing switch was implemented or enabled.
- The extraction safety guard is intentionally conservative: quoted, negated or compound phrasing is clarified even when a human might read a supported compound.
