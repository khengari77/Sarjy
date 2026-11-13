# Memory & context explorer

`MemoryPanel.svelte` is a self-contained panel for the transparent memory
contract: every stored fact appears with its source, revision and timestamp,
and each row distinguishes stored / retrieved / included / excluded states.
It never invents records and never claims a save, exclusion or deletion the
server did not confirm.

## Mounting in the Memories tab

The Living Cloud shell (`RightDrawer`, PR #120) is not on this branch, so this
panel does not create a duplicate shell. Mount it once inside the right drawer's
Memories tab, replacing the tab that currently renders `VoiceSettings`:

```svelte
<MemoryPanel conversationId={conversation.current} />
```

with:

```ts
import MemoryPanel from '$lib/memory/MemoryPanel.svelte';
```

`conversation.current` may be `null`; the panel then shows the owner's stored
facts without included/excluded annotations and disables exclusion actions.

## Behaviour

- `GET /v1/memories?conversation_id=…&limit=…&offset=…` is owner-scoped and
  paginated. `conversation_id` is optional and only adds the
  `included_in_conversation` / `excluded_in_conversation` annotations.
- Search uses the existing `POST /v1/memories/search`; results are labelled
  approximate and "matched search", never conflated with actual model input.
- "Exclude…" calls `POST /v1/conversations/{id}/context/exclusions` to sign a
  preview that writes nothing. The preview lists the exact record and diff.
- Confirmation calls the existing `POST …/context/confirm` with the signed
  token and known revision. The applied diff comes from the server response.
- Undo calls the existing `POST …/context/undo`; it restores only that
  operation and shows the new revision.
- Exclusions are conversation-scoped and reversible. They omit the fact from
  the next compiled context (verified by
  `tests/test_context_exclusion_policy.py`), but do not rewrite history and do
  not delete the stored fact.

## Tests

```sh
pnpm --dir apps/web test:unit -- src/lib/memory/memory.test.ts
pnpm --dir apps/web check
pnpm --dir apps/web lint
```
