<script lang="ts">
  import MemoryRowView from './MemoryRow.svelte';
  import type { MemoryRow } from './memory';

  interface Props {
    rows: MemoryRow[];
    loading: boolean;
    error: string;
    total: number;
    hasMore: boolean;
    loadingMore: boolean;
    busy: Record<string, boolean>;
    rowErrors: Record<string, string>;
    searchRan: boolean;
    outsideSearchMatches: number;
    onretry: () => void;
    onloadmore: () => void;
    onhide: (row: MemoryRow) => void;
    onrestore: (row: MemoryRow) => void;
    ondelete: (row: MemoryRow, trigger: HTMLElement) => void;
  }

  let {
    rows,
    loading,
    error,
    total,
    hasMore,
    loadingMore,
    busy,
    rowErrors,
    searchRan,
    outsideSearchMatches,
    onretry,
    onloadmore,
    onhide,
    onrestore,
    ondelete,
  }: Props = $props();
</script>

<section class="memory-list" aria-label="Saved facts" data-testid="memory-list-active">
  {#if loading}
    <p class="memory-muted">Loading saved facts…</p>
  {:else if error}
    <div class="memory-error-block" role="alert">
      <p>{error}</p>
      <button class="ghost-button" onclick={onretry}>Retry</button>
    </div>
  {:else if rows.length === 0}
    <div class="memory-empty">
      <p>No saved facts yet.</p>
      <p class="memory-muted">Facts Sarjy saves will appear here with their source. Nothing is invented.</p>
    </div>
  {:else}
    <p class="memory-count">
      {rows.length} shown of {total} stored {total === 1 ? 'fact' : 'facts'}
    </p>
    {#if searchRan}
      <p class="memory-muted">Highlighted rows matched this search. Matches are approximate.</p>
    {/if}
    {#if outsideSearchMatches > 0}
      <p class="memory-muted" role="status">
        {outsideSearchMatches} search {outsideSearchMatches === 1 ? 'match is' : 'matches are'} outside the loaded page. Load
        more to inspect them.
      </p>
    {/if}
    <ul class="memory-rows">
      {#each rows as row (row.item.id)}
        <MemoryRowView
          {row}
          busy={busy[row.item.id] === true}
          error={rowErrors[row.item.id] ?? ''}
          onhide={() => onhide(row)}
          onrestore={() => onrestore(row)}
          ondelete={(trigger) => ondelete(row, trigger)}
        />
      {/each}
    </ul>
    {#if hasMore}
      <button class="ghost-button memory-load-more" disabled={loadingMore} onclick={onloadmore}>
        {loadingMore ? 'Loading…' : 'Load more'}
      </button>
    {/if}
  {/if}
</section>
