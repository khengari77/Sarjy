<script lang="ts">
  import ExclusionOutcome from './ExclusionOutcome.svelte';
  import ExclusionReview from './ExclusionReview.svelte';
  import MemoryCandidateList from './MemoryCandidateList.svelte';
  import MemoryIntro from './MemoryIntro.svelte';
  import MemorySearch from './MemorySearch.svelte';
  import { createMemoryExplorer } from './explorer.svelte';
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
</script>

<section class="memory-panel" aria-label="Memory and context explorer" data-testid="memory-panel">
  <MemoryIntro {conversationId} />

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
    canExclude={Boolean(conversationId)}
    searchRan={explorer.searchRan}
    outsideSearchMatches={explorer.outsideSearchMatches}
    onretry={() => void explorer.reload()}
    onloadmore={() => void explorer.loadMore()}
    onexclude={(row) => void explorer.beginExclusion(row)}
  />

  {#if explorer.previewLoading}
    <p class="memory-muted" role="status">Preparing a preview. Nothing has changed.</p>
  {/if}

  {#if explorer.previewError}
    <div class="memory-error-block" role="alert">
      <p>{explorer.previewError}</p>
      <button class="ghost-button" onclick={() => explorer.cancelExclusion()}>Dismiss</button>
    </div>
  {/if}

  {#if explorer.preview && explorer.selected}
    <ExclusionReview
      preview={explorer.preview}
      title={`Exclude “${explorer.selected.item.content.slice(0, 80)}${
        explorer.selected.item.content.length > 80 ? '…' : ''
      }”?`}
      busy={explorer.applying}
      error={explorer.applyError}
      onconfirm={() => void explorer.confirm()}
      oncancel={() => explorer.cancelExclusion()}
    />
  {/if}

  <ExclusionOutcome
    confirmation={explorer.confirmation}
    undoResult={explorer.undoResult}
    undoBusy={explorer.undoBusy}
    undoError={explorer.undoError}
    onundo={() => void explorer.undo()}
  />
</section>
