# Memory & context explorer

`MemoryPanel.svelte` is a self-contained panel for the transparent memory
contract: every stored fact appears with its source, revision and timestamp,
and each row distinguishes stored / retrieved / included / excluded states.
It never invents records and never claims a save, exclusion or deletion the
server did not confirm.

## Mounting in the Memories tab

The Living Cloud shell (`RightDrawer`, set up in #120) owns the right drawer.
This panel does not create a duplicate shell: `RightDrawer` renders it through
its optional `memoryPanel` snippet in the Memories tab, and
`apps/web/src/routes/+page.svelte` supplies it once:

```svelte
<RightDrawer {arabic}>
  {#snippet memoryPanel()}
    <MemoryPanel conversationId={conversation.current} />
  {/snippet}
  ...
</RightDrawer>
```

with:

```ts
import MemoryPanel from '$lib/memory/MemoryPanel.svelte';
```

The Context tab points at the Memories tab for the inclusion/exclusion
projection; it does not mount a second copy of the panel.

`conversation.current` may be `null`; the panel then shows the owner's stored
facts without candidates/exclusion annotations and disables exclusion actions.

## Behaviour

- `GET /v1/memories?conversation_id=…&limit=…&offset=…` is owner-scoped and
  paginated. `conversation_id` is optional and only adds the
  `candidate_eligible` / `included_in_conversation` / `excluded_in_conversation`
  annotations.
- The list's projection comes from the compiler's own reader
  (`orm_memory_candidates`), so exclusion _lineage_ applies: a correction that
  supersedes an excluded fact is excluded too, and the bounded candidate window
  is not backfilled after exclusions.
- **Candidate, not included.** `included_in_conversation` means "inside the
  candidate set the next prompt is built from". The compiler can still trim
  that prompt to its character budget, so the UI calls this a _candidate_ and
  never claims the model received the fact. `candidate_eligible` is weaker
  still: active and not excluded, but possibly outside the candidate window.
- `kind` is `budget` only when the classifier returns exactly `budget`; an
  ordinary fact is `memory`.
- The panel reloads when the conversation changes, when a finalised turn
  changes the memory revision, and when the Memories tab is (re)opened while
  the list is stale — a voice save made while the tab was hidden shows up
  without switching conversations.
- Search uses the existing `POST /v1/memories/search`; results are labelled
  approximate and "matched search", never conflated with actual model input.
- "Exclude…" calls `POST /v1/conversations/{id}/context/exclusions` to sign a
  preview that writes nothing. The preview lists the exact record and diff.
- Confirmation calls the existing `POST …/context/confirm` with the signed
  token and known revision. The applied diff comes from the server response.
- Undo calls the existing `POST …/context/undo`; it restores only that
  operation and shows the new revision.
- A mutation whose response is lost (timeout, dropped connection, 5xx) is
  reported as an **unknown outcome**, never as "nothing was changed".
- Exclusions are conversation-scoped and reversible. They omit the fact from
  the next compiled context (verified by
  `tests/test_context_exclusion_policy.py`), but do not rewrite history and do
  not delete the stored fact.

## Deferred (recorded on #122)

- Operation results are not yet fenced to their originating conversation: a
  confirmation that resolves after a conversation switch, and a retry that
  reuses a fresh idempotency key after an unknown outcome. Repro and intended
  fix are in the PR body.

## Tests

```sh
pnpm --dir apps/web test:unit -- src/lib/memory/memory.test.ts
pnpm --dir apps/web check
pnpm --dir apps/web lint
```
