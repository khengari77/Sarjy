<script lang="ts">
  import MemoryIconButton from './MemoryIconButton.svelte';
  import type { MemoryRow } from './memory';
  import { formatTimestamp, provenance, shortId, statusLabel } from './memory';

  interface Props {
    row: MemoryRow;
    busy: boolean;
    error: string;
    onhide: () => void;
    onrestore: () => void;
    ondelete: (trigger: HTMLElement) => void;
  }

  let { row, busy, error, onhide, onrestore, ondelete }: Props = $props();

  const hidden = $derived(row.item.status === 'hidden');
  // Only current versions can be hidden or deleted; earlier versions are read-only.
  const actionable = $derived(row.item.status === 'active' || hidden);
</script>

<li
  class="memory-row"
  data-testid="memory-row"
  data-memory-id={row.item.id}
  data-hidden={hidden}
  data-excluded={row.labels.excluded}
  data-included={row.labels.candidate}
  data-eligible={row.labels.eligible}
  aria-busy={busy}
>
  <div class="memory-row-main">
    <div class="memory-row-text">
      <p class="memory-content">{row.item.content}</p>
      {#if !hidden}
        <p class="memory-summary">{row.labels.summary}</p>
        <ul class="memory-chips" aria-label="States for this fact">
          <li class="chip chip-stored">Stored</li>
          {#if row.labels.retrieved}<li class="chip chip-retrieved">Matched search</li>{/if}
          {#if row.labels.candidate}<li class="chip chip-included">Candidate</li>{/if}
          {#if row.labels.excluded}<li class="chip chip-excluded">Excluded</li>{/if}
        </ul>
      {:else}
        <p class="memory-summary">Sarjy does not use this memory anywhere while it is hidden.</p>
        <ul class="memory-chips" aria-label="States for this fact">
          <li class="chip chip-hidden">Hidden</li>
        </ul>
      {/if}
    </div>
    {#if actionable}
      <div class="memory-icon-actions">
        <!-- One toggle: the same button flips between Hide and Restore. -->
        <MemoryIconButton kind={hidden ? 'restore' : 'hide'} disabled={busy} onclick={hidden ? onrestore : onhide} />
        <MemoryIconButton
          kind="delete"
          disabled={busy}
          onclick={(event) => ondelete(event.currentTarget as HTMLElement)}
        />
      </div>
    {/if}
  </div>
  {#if error}
    <p class="memory-row-error" role="status" data-testid="memory-row-error">{error}</p>
  {/if}
  <p class="memory-provenance">
    {formatTimestamp(row.item.updated_at)} · revision {row.item.version}
    · {statusLabel(row.item.status)}
  </p>
  <p class="memory-provenance">{provenance(row.item)}</p>
  <p class="memory-provenance memory-id" aria-label="Record id"><code>{shortId(row.item.id)}</code></p>
</li>
