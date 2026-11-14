# Decision layer (issue #12 headstart)

Status: **pure domain seam only**. No route, worker call site, UI, handler or
enablement is wired. Automatic Jev routing remains disabled and still requires a
reviewed live #13 gate.

## Five-question batch

`apps/api/app/domain/interaction.py` defines the versioned
`interaction-questions-v1` contract with exactly the five questions from the
approved #12 supplement:

1. `request_type` — answer / action / both / unclear;
2. `action_family` — the six action families plus `not_applicable` and `unclear`;
3. `operation` — registered operation / `not_applicable` / `unclear`;
4. `pending_relation` — new / supply details / confirm / reject / modify / cancel /
   unrelated / unclear;
5. `scope_clarity` — clear / missing information / ambiguous / not_applicable.

`not_applicable` and `unclear` are separate enum values, not aliases. A registered
operation must name an exact registry id. The batch does not carry executable
arguments; specialized extraction supplies arguments only after the operation is
chosen.

## State machine

`transition(current_state, validated_answers, pending=..., arguments=...)`
returns one immutable `Transition` carrying a clarification, a pending proposal,
a ready action or a terminal answer. The lifecycle is:

`Interpreting → Awaiting details / Awaiting confirmation / Ready → Executing →
Succeeded / Failed / Unknown / Cancelled`.

Contradictory answers, unknown operation ids, family/operation mismatches, missing
reply-to-pending bindings, scope ambiguity, missing arguments and confidence below
`CONFIDENCE_THRESHOLD` can only produce clarification, detail or confirmation
states. They cannot produce `ReadyAction`.

`begin_execution` is the sole constructor of `EffectRequest`. It refuses every
source state except `READY`, refuses a confirmation-bound action before the
confirmation was delivered, and revalidates the operation arguments. The effect
seam therefore cannot be reached from `Interpreting`, `Awaiting details`,
`Awaiting confirmation`, `Executing` or any terminal state.

## Caller-owned operations

This headstart deliberately does not define an action registry, argument schemas
or command parsers; #45 owns those. `transition(...)` accepts two caller-supplied
collections: `known_operations` and `confirmation_required`. A registered id that
is not in `known_operations` is treated as unknown and cannot reach a ready
action. The default empty collections therefore fail closed.

## Verification

`tests/test_interaction_decisions.py` exhaustively combines every state, answer
choice, pending variant and confidence band, checks every transition, and uses a
Hypothesis property that no non-READY source can begin an effect. It also checks
that low confidence cannot authorize any registered operation and that
confirmation must bind to the delivered operation/revision/arguments.

`tests/test_day2_offline_gate.py` produces and checks the offline gate record in
`docs/day2-offline-gate-evidence.json`.
