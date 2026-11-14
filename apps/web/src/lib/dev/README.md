# Developer mode (issue #46)

Simple / Developer is a presentation-only depth switch for the right panel. It
is deliberately **read-only**: it inspects stored context, revision history and
diffs; the only context mutations remain the approved confirm/undo flow owned
by #43, which is not part of this module.

## Mount point

`apps/web/src/routes/+page.svelte` renders `DevModePanel` as the last block of
the `right` snippet, directly after `VoiceSettings`:

```svelte
<DevModePanel conversationId={conversation.current} {arabic} />
```

The working tree does not contain the tabbed `RightDrawer` (#120) or the memory
panel (#122). When #120 lands, move this component into the **Context** tab
(`Memories · Voice · Appearance · Context`) and delete the mount comment; do not
duplicate the drawer shell. The panel owns its own mode control, so no permanent
“Simple” pill is required in the header.

## Gating and persistence

- The selected mode is stored in `localStorage` under `sarjy-ui-mode`
  (`simple` | `developer`). It is cosmetic and survives panel close and the
  language preference.
- Entering Developer mode runs `authorizeDeveloper()`, a read-only
  `GET /v1/conversations/{id}/context`. The same owner-scoped identity check that
  protects the backend decides whether the control is rendered at all. A 401,
  403 or 404 keeps the panel in Simple mode; no Developer control is reachable
  by CSS alone.
- An authorization outage (`unavailable`) also stays in Simple mode and reports
  `DEV_MODE_UNAVAILABLE`, with a Retry action. The stored preference is kept for
  the next successful check; a denial clears it.
- Leaving Developer mode is local and immediate: no request is issued.
- Toggling never POSTs `/v1/voice/sessions`, dispatches an agent, resets the orb
  or changes the microphone. The panel only reads
  `GET .../context` and `GET .../context/operations`.

## Honest data

`GET .../context` returns only `conversation_id`, `revision` and `excluded_ids`;
`GET .../context/operations` adds command type, outcome, revisions, timestamps
and a stored diff of record IDs. The panel therefore shows:

- compiled-context revision and exclusions (available),
- provenance and before/after diffs from stored operations (available),
- retrieved-vs-included record text, token counts and a latency breakdown as
  **unavailable**, because the API does not return them,
- the renderer tier computed locally from reduced-motion and WebGL availability.

Responses are mapped field by field (`contextApi.ts`); idempotency keys,
provider metadata and unknown fields are dropped before rendering.

## Tests

- `mode.test.ts` — gating, persistence, demo/denial/outage behaviour and that
  disabling mode makes no server call.
- `contextApi.test.ts` — GET-only reads, whitelisted mapping, honest
  unauthorized/unavailable errors, no writes.
- `panelModel.test.ts` — read-only rendering, honest empty/unavailable states and
  no secret material in the view model.
