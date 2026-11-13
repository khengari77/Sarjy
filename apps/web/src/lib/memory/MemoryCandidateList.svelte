<script lang="ts">
  import type { MemoryRow } from './memory';
  import { formatTimestamp, provenance, shortId, statusLabel } from './memory';

  interface Props {
    rows: MemoryRow[];
    loading: boolean;
    error: string;
    total: number;
    hasMore: boolean;
    loadingMore: boolean;
    canExclude: boolean;
    searchRan: boolean;
    outsideSearchMatches: number;
    onretry: () => void;
    onloadmore: () => void;
    onexclude: (row: MemoryRow) => void;
  }

  let {
    rows,
    loading,
    error,
    total,
    hasMore,
    loadingMore,
    canExclude,
    searchRan,
    outsideSearchMatches,
    onretry,
    onloadmore,
    onexclude,
  }: Props = $props();
</script>

<section class="memory-list" aria-label="Saved facts">
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
        <li class="memory-row" data-excluded={row.labels.excluded} data-included={row.labels.included}>
          <p class="memory-content">{row.item.content}</p>
          <p class="memory-summary">{row.labels.summary}</p>
          <ul class="memory-chips" aria-label="States for this fact">
            <li class="chip chip-stored">Stored</li>
            {#if row.labels.retrieved}<li class="chip chip-retrieved">Matched search</li>{/if}
            {#if row.labels.included}<li class="chip chip-included">Included</li>{/if}
            {#if row.labels.excluded}<li class="chip chip-excluded">Excluded</li>{/if}
          </ul>
          <p class="memory-provenance">
            {formatTimestamp(row.item.updated_at)} · revision {row.item.version} ·
            {statusLabel(row.item.status)}
          </p>
          <p class="memory-provenance">{provenance(row.item)}</p>
          <p class="memory-provenance memory-kind">{row.item.kind === 'budget' ? 'Money-related fact' : 'Fact'}</p>
          <div class="memory-row-actions">
            <button
              class="ghost-button"
              disabled={!canExclude}
              title={canExclude ? 'Review before excluding' : 'Select a conversation first'}
              onclick={() => onexclude(row)}
            >
              Exclude…
            </button>
            <span class="memory-id" aria-label="Record id"><code>{shortId(row.item.id)}</code></span>
          </div>
        </li>
      {/each}
    </ul>
    {#if hasMore}
      <button class="ghost-button memory-load-more" disabled={loadingMore} onclick={onloadmore}>
        {loadingMore ? 'Loading…' : 'Load more'}
      </button>
    {/if}
  {/if}
</section>
