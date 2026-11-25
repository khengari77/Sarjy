<script lang="ts">
  import ContextStrip from '../context/ContextStrip.svelte';
  import DeleteMemoryDialog from './DeleteMemoryDialog.svelte';
  import MemoryCandidateList from './MemoryCandidateList.svelte';
  import MemoryIntro from './MemoryIntro.svelte';
  import MemorySearch from './MemorySearch.svelte';
  import { createMemoryExplorer } from './explorer.svelte';
  import type { MemoryRow } from './memory';
  import './memory.css';

  interface Props {
    conversationId: string | null;
    /** The Memories tab is visible; opening it refreshes a stale list. */
    active?: boolean;
    /** Changes when a turn finalises (memory capture) or the chat changes. */
    revision?: string;
  }

  let { conversationId, active = false, revision = '' }: Props = $props();

  const explorer = createMemoryExplorer({
    conversationId: () => conversationId,
    active: () => active,
    revision: () => revision,
  });

  let panelEl = $state<HTMLElement | null>(null);
  let pendingDelete = $state<{ row: MemoryRow; trigger: HTMLElement } | null>(null);
  let deleting = $state(false);

  function askDelete(row: MemoryRow, trigger: HTMLElement) {
    pendingDelete = { row, trigger };
  }

  async function confirmDelete() {
    const target = pendingDelete;
    if (!target || deleting) return;
    deleting = true;
    await explorer.remove(target.row.item.id);
    deleting = false;
    // On failure the row stays and carries its own inline message.
    pendingDelete = null;
  }
</script>

<section
  class="memory-panel"
  bind:this={panelEl}
  tabindex="-1"
  aria-label="Memory and context explorer"
  data-testid="memory-panel"
>
  <MemoryIntro {conversationId} />

  <ContextStrip {conversationId} {revision} />

  <MemorySearch
    bind:query={explorer.query}
    searching={explorer.searching}
    error={explorer.searchError}
    disabled={!conversationId}
    onsearch={(value) => void explorer.search(value)}
  />

  {#if explorer.searchRan && explorer.searchResults.length === 0}
    <p class="memory-muted" role="status">No saved fact matched this search.</p>
  {/if}

  <MemoryCandidateList
    rows={explorer.rows}
    loading={explorer.loading}
    error={explorer.listError}
    total={explorer.total}
    hasMore={explorer.nextOffset !== null}
    loadingMore={explorer.loadingMore}
    busy={explorer.busy}
    rowErrors={explorer.rowErrors}
    searchRan={explorer.searchRan}
    outsideSearchMatches={explorer.outsideSearchMatches}
    onretry={() => void explorer.reload()}
    onloadmore={() => void explorer.loadMore()}
    onhide={(row) => void explorer.hide(row.item.id)}
    onrestore={(row) => void explorer.restore(row.item.id)}
    ondelete={askDelete}
  />

  {#if pendingDelete}
    <DeleteMemoryDialog
      content={pendingDelete.row.item.content}
      busy={deleting}
      trigger={pendingDelete.trigger}
      fallback={panelEl}
      ondelete={() => void confirmDelete()}
      oncancel={() => (pendingDelete = null)}
    />
  {/if}
</section>
