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
states. They cannot produce `ReadyAction`. A confirmation is accepted only from
`Awaiting confirmation`; from any other state it is a clarification, so `Ready`
cannot be confirmed twice into a second action at the same revision. A
`missing_information` scope on a confirm turn is treated as supplying details,
not as confirmation, and returns to `Awaiting details` with an incremented
revision.

## Execution seam

`begin_execution(transition, *, idempotency_key)` authorizes one effect and is the
only domain function that produces an `EffectRequest`. It takes the `Transition`
itself, requires `transition.state is READY` and `transition.ready is not None`,
and copies the action from it. A bare `ReadyAction` is rejected as
`READY_TRANSITION_REQUIRED`, and the `Transition` model already refuses to attach
a ready action to a non-`READY` state, so the pairing is structural rather than
two independently supplied arguments. It also refuses a confirmation-bound action
before delivery.

`idempotency_key` is required and caller-supplied, with no default. It must be
unique across subjects and conversations because `context_operations.idempotency_key`
is `unique=True` in the ledger; namespace it with the owner and conversation scope
(for example `f"{owner_id}:{conversation_id}:{operation_id}:{revision}"`) or use
the context-operation digest scheme. Deriving it from operation and revision alone
would collide between users executing the same operation at revision 1. The ledger
column is 100 characters, so keep the key within that limit even though the domain
model allows 200.

Enforced limits that callers must not assume away:

* **No argument validation.** `begin_execution` validates no operation arguments
  and no argument schema, because #45 owns those. The handler must validate before
  acting; an accepted `EffectRequest` means "authorized by the state machine", not
  "arguments are valid".
* **`EffectRequest` is publicly constructible.** It is a Pydantic contract like
  the others. The seam is a domain convention at call sites, not a Python access
  restriction; nothing prevents code from instantiating the model directly.
* **The binding is to the step, not to issuance.** `Transition` is also publicly
  constructible, so code that hand-builds a `READY` transition can still attach a
  hand-built `ReadyAction`. What is enforced is that execution reads the action
  from the transition it was given, never from a separately supplied action; the
  state machine remains the only intended producer of that transition.
* **Immutability is shallow.** Freezing copies the top-level mapping only; nested
  containers are still shared with the caller and must not be mutated.

## Caller-owned operations

This headstart deliberately does not define an action registry, argument schemas
or command parsers; #45 owns those. `transition(...)` accepts two caller-supplied
collections: `known_operations` and `confirmation_required`. A registered id that
is not in `known_operations` is treated as unknown and cannot reach a ready
action. The default empty collections therefore fail closed.

## Verification

`tests/test_interaction_decisions.py` exhaustively combines every state, answer
choice, pending variant and confidence band, checks every transition, and uses a
Hypothesis property that only a `READY` transition carrying an action can begin
an effect and that a bare `ReadyAction` cannot. It also checks that low
confidence cannot authorize any registered operation, that confirmation binds to
the delivered operation/revision/arguments, that confirmation only works from
`Awaiting confirmation`, and that the idempotency key is required.

`tests/test_day2_offline_gate.py` produces and checks the offline gate record in
`docs/day2-offline-gate-evidence.json`.
