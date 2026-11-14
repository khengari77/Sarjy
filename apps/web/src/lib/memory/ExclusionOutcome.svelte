<script lang="ts">
  import ContextDiff from './ContextDiff.svelte';
  import type { ContextConfirmation, ContextUndoResult } from './memory';

  interface Props {
    confirmation: ContextConfirmation | null;
    undoResult: ContextUndoResult | null;
    undoBusy: boolean;
    undoError: string;
    onundo: () => void;
  }

  let { confirmation, undoResult, undoBusy, undoError, onundo }: Props = $props();
</script>

{#if confirmation}
  <section class="memory-applied" aria-label="Exclusion confirmed" data-testid="exclusion-confirmed">
    <h4>Exclusion confirmed</h4>
    <p>
      {confirmation.excluded_ids.length} fact
      {confirmation.excluded_ids.length === 1 ? 'is' : 'are'} now omitted from this conversation's next compiled context.
      The fact remains stored; conversation history is not rewritten.
    </p>
    <ContextDiff diff={confirmation.diff} title="Applied change" removedLabel="Excluded" />
    {#if undoError}<p class="memory-error" role="alert">{undoError}</p>{/if}
    <button class="ghost-button" onclick={onundo} disabled={undoBusy}>
      {undoBusy ? 'Undoing…' : 'Undo exclusion'}
    </button>
  </section>
{/if}

{#if undoResult}
  <section class="memory-applied" aria-label="Exclusion undone" data-testid="exclusion-undone">
    <h4>Exclusion undone</h4>
    <p>
      {undoResult.restored_ids.length} fact
      {undoResult.restored_ids.length === 1 ? 'is' : 'are'} eligible for this conversation's context again.
    </p>
    <ContextDiff diff={undoResult.diff} title="Restored change" keptLabel="Still excluded" />
  </section>
{/if}
