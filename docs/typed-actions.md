# Typed actions: issue #7

The application accepts validated actions through `POST /v1/actions`. Both UI and finalized-transcript
submissions use the same owner-authorized dispatcher. `GET /v1/settings` reads only the current identity's
settings. Existing development access protection applies to these routes.

A submission contains `conversation_id`, `request_id`, `expected_revision`, `source` (`ui` or `transcript`),
and exactly one of `action` (the existing discriminated action object) or `transcript`. `finalized` defaults
to true and cannot be false. Rejected partial transcripts never reach a handler.

For settings actions, `expected_revision` is the settings revision returned by GET settings. Relative
speed changes use the domain's 0.1 step and 0.8–1.2 bounds. Selected voices are validated against the
configured approved catalog before persistence. No-op settings actions do not create a settings revision.
Presentation actions return a typed navigation result; the later shared frontend reducer can apply it.
No arbitrary JavaScript or model-generated frontend operation is executed.

The exact English fast path recognizes only these whole utterances (case/whitespace normalization and
one trailing period are accepted): speak slower, speak faster, hide captions, show captions, show my
previous conversations, open the memory explorer. Negated, quoted, compound, unsupported or nonexact
utterances return `handled: false` and make no changes. Full voice-controlled UI wiring remains Day 3.

Idempotency binds the owner and request ID to the conversation, expected revision and normalized action.
Equivalent UI and transcript submissions share a replay result. A changed payload with the same key
returns 409. Transactions serialize through the existing session gate, and a completed result is recorded
in a namespaced ContextOperation. Settings and operation history commit together.

Memory/context actions are delegated only to explicitly registered `app.state.action_handlers[kind]`.
An absent handler returns `ACTION_HANDLER_UNAVAILABLE`, not a fabricated success. A handler receives
keyword arguments `db`, `owner_id`, `conversation_id`, `request_id`, `expected_revision`, `action`.
It receives a restricted effect-session capability and must validate its resource ownership/revision and return JSON-compatible results. Commit, rollback and begin are forbidden;
the dispatcher commits one transaction after recording its replay result. Proposal confirmation cannot
be invented by a classifier: the existing action union has no confirm operation.

This PR adds no migration, paid inference, speech synthesis or hosted acceptance claim. It does not
rewrite existing worker generation routing. The transcript adapter is directly testable through the same
API as the UI adapter, ready for later finalized voice ingress integration.


The dispatcher uses a dedicated transaction, so replay and failures release its locks without touching
the authentication session. Voice catalog validation runs before the global mutation gate. A handler
may expose an async `prepare` method/attribute with the same named inputs; it runs with a clean read
session before locking and returns data passed to the handler as `prepared`. Use this phase for semantic
retrieval with separately guarded embeddings. Preparation cannot mutate business state. The effect
handler must revalidate revision/scope before consuming prepared results. No prepare hook means the
existing handler call signature is unchanged.
