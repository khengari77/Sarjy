<script lang="ts">
  import type { ContextProposal } from './memory';
  import ContextDiff from './ContextDiff.svelte';

  interface Props {
    preview: ContextProposal;
    title: string;
    busy: boolean;
    error: string;
    onconfirm: () => void;
    oncancel: () => void;
  }

  let { preview, title, busy, error, onconfirm, oncancel }: Props = $props();
</script>

<section class="memory-review" aria-label="Confirm exclusion" data-testid="exclusion-review">
  <h4>{title}</h4>
  <p class="memory-caution">
    Preview only — nothing has changed yet. Confirming omits the fact from this conversation's next compiled context.
    The fact stays stored, and the change can be undone.
  </p>
  {#if preview.matches.length}
    <ul class="memory-review-matches">
      {#each preview.matches as match (match.id)}
        <li>
          <p class="memory-content">{match.content}</p>
          <p class="memory-provenance">Record <code>{match.id.slice(0, 8)}…</code></p>
        </li>
      {/each}
    </ul>
  {/if}
  {#if preview.preserved.length}
    <div class="memory-preserved">
      <p class="memory-diff-label">Preserved</p>
      <ul>
        {#each preview.preserved as record (record.id)}
          <li>
            <p>{record.content}</p>
            <p class="memory-muted">{record.reason}</p>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
  {#if preview.ambiguous.length}
    <p class="memory-error" role="alert">
      Some records need a clear decision before they can be excluded. Nothing has changed.
    </p>
  {/if}
  <ContextDiff
    diff={preview.diff}
    title="Proposed change"
    removedLabel="Will be excluded from this conversation"
    keptLabel="Preserved"
  />
  {#if error}
    <p class="memory-error" role="alert">{error}</p>
  {/if}
  <div class="memory-actions">
    <button class="primary-button" onclick={onconfirm} disabled={busy || preview.diff.removed.length === 0}>
      {busy ? 'Confirming…' : 'Confirm exclusion'}
    </button>
    <button class="ghost-button" onclick={oncancel} disabled={busy}>Cancel</button>
  </div>
</section>
