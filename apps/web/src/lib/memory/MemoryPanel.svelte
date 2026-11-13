<script lang="ts">
  import { errorCode } from '../voice/errors';
  import ExclusionOutcome from './ExclusionOutcome.svelte';
  import ExclusionReview from './ExclusionReview.svelte';
  import MemoryCandidateList from './MemoryCandidateList.svelte';
  import MemorySearch from './MemorySearch.svelte';
  import {
    confirmExclusion,
    friendlyMemoryError,
    labelsFor,
    loadMemories,
    newIdempotencyKey,
    previewExclusion,
    retrievedIds,
    searchMemories,
    undoExclusion,
    type ContextConfirmation,
    type ContextProposal,
    type ContextUndoResult,
    type MemoryListItem,
    type MemoryRow,
    type MemorySearchResult,
  } from './memory';
  import './memory.css';

  interface Props {
    conversationId: string | null;
  }

  let { conversationId }: Props = $props();

  let items = $state<MemoryListItem[]>([]);
  let total = $state(0);
  let nextOffset = $state<number | null>(null);
  let loading = $state(true);
  let loadingMore = $state(false);
  let listError = $state('');
  let query = $state('');
  let searching = $state(false);
  let searchError = $state('');
  let searchRan = $state(false);
  let searchResults = $state<MemorySearchResult[]>([]);
  let selected = $state<MemoryRow | null>(null);
  let preview = $state<ContextProposal | null>(null);
  let previewLoading = $state(false);
  let previewError = $state('');
  let applying = $state(false);
  let applyError = $state('');
  let confirmation = $state<ContextConfirmation | null>(null);
  let undoBusy = $state(false);
  let undoError = $state('');
  let undoResult = $state<ContextUndoResult | null>(null);
  let loadedFor = $state<string | null | undefined>(undefined);

  const retrieved = $derived(retrievedIds(searchResults));
  const rows = $derived<MemoryRow[]>(items.map((item) => ({ item, labels: labelsFor(item, retrieved.has(item.id)) })));
  const outsideSearchMatches = $derived(
    searchRan ? searchResults.filter((result) => !items.some((item) => item.id === result.id)).length : 0,
  );

  $effect(() => {
    if (loadedFor === conversationId) return;
    loadedFor = conversationId;
    searchResults = [];
    searchRan = false;
    searchError = '';
    preview = null;
    confirmation = null;
    undoResult = null;
    void reload();
  });

  async function reload() {
    const requested = conversationId;
    loading = true;
    listError = '';
    try {
      const page = await loadMemories(requested, 0);
      if (requested !== conversationId) return;
      items = page.items;
      total = page.total;
      nextOffset = page.next_offset;
    } catch (error) {
      if (requested !== conversationId) return;
      items = [];
      total = 0;
      nextOffset = null;
      listError = friendlyMemoryError(errorCode(error));
    } finally {
      if (requested === conversationId) loading = false;
    }
  }

  async function loadMore() {
    if (nextOffset === null) return;
    const requested = conversationId;
    loadingMore = true;
    listError = '';
    try {
      const page = await loadMemories(requested, nextOffset);
      if (requested !== conversationId) return;
      items = [...items, ...page.items];
      total = page.total;
      nextOffset = page.next_offset;
    } catch (error) {
      if (requested !== conversationId) return;
      listError = friendlyMemoryError(errorCode(error));
    } finally {
      if (requested === conversationId) loadingMore = false;
    }
  }

  async function search(value: string) {
    if (!conversationId) return;
    const requested = conversationId;
    searching = true;
    searchError = '';
    try {
      const results = await searchMemories(requested, value);
      if (requested !== conversationId) return;
      searchResults = results;
      searchRan = true;
    } catch (error) {
      if (requested !== conversationId) return;
      searchResults = [];
      searchRan = false;
      searchError = friendlyMemoryError(errorCode(error));
    } finally {
      if (requested === conversationId) searching = false;
    }
  }

  async function beginExclusion(row: MemoryRow) {
    if (!conversationId) return;
    const requested = conversationId;
    selected = row;
    preview = null;
    previewError = '';
    applyError = '';
    confirmation = null;
    undoResult = null;
    undoError = '';
    previewLoading = true;
    try {
      const proposal = await previewExclusion(requested, [row.item.id]);
      if (requested !== conversationId) return;
      preview = proposal;
    } catch (error) {
      if (requested !== conversationId) return;
      previewError = friendlyMemoryError(errorCode(error));
    } finally {
      if (requested === conversationId) previewLoading = false;
    }
  }

  function cancelExclusion() {
    selected = null;
    preview = null;
    previewError = '';
    applyError = '';
  }

  async function confirm() {
    if (!conversationId || !preview) return;
    applying = true;
    applyError = '';
    try {
      confirmation = await confirmExclusion(
        conversationId,
        preview.proposal,
        preview.revision,
        newIdempotencyKey('memory-exclude'),
      );
      preview = null;
      selected = null;
      await reload();
    } catch (error) {
      applyError = friendlyMemoryError(errorCode(error));
    } finally {
      applying = false;
    }
  }

  async function undo() {
    if (!conversationId || !confirmation) return;
    undoBusy = true;
    undoError = '';
    try {
      undoResult = await undoExclusion(
        conversationId,
        confirmation.operation_id,
        confirmation.revision_after,
        newIdempotencyKey('memory-undo'),
      );
      confirmation = null;
      await reload();
    } catch (error) {
      undoError = friendlyMemoryError(errorCode(error));
    } finally {
      undoBusy = false;
    }
  }
</script>

<section class="memory-panel" aria-label="Memory and context explorer" data-testid="memory-panel">
  <header class="memory-header">
    <h3>Memory &amp; context</h3>
    <p class="memory-muted">Every saved fact is visible here with its source; states stay separate.</p>
  </header>

  {#if !conversationId}
    <p class="memory-notice" role="status">
      Select a conversation to see whether a fact is included or excluded. Stored facts are listed below.
    </p>
  {/if}

  <MemorySearch
    bind:query
    {searching}
    error={searchError}
    disabled={!conversationId}
    onsearch={(value) => void search(value)}
  />

  {#if searchRan && searchResults.length === 0}
    <p class="memory-muted" role="status">No saved fact matched this search.</p>
  {/if}

  <MemoryCandidateList
    {rows}
    {loading}
    error={listError}
    {total}
    hasMore={nextOffset !== null}
    {loadingMore}
    canExclude={Boolean(conversationId)}
    {searchRan}
    {outsideSearchMatches}
    onretry={() => void reload()}
    onloadmore={() => void loadMore()}
    onexclude={(row) => void beginExclusion(row)}
  />

  {#if previewLoading}
    <p class="memory-muted" role="status">Preparing a preview. Nothing has changed.</p>
  {/if}

  {#if previewError}
    <div class="memory-error-block" role="alert">
      <p>{previewError}</p>
      <button class="ghost-button" onclick={cancelExclusion}>Dismiss</button>
    </div>
  {/if}

  {#if preview && selected}
    <ExclusionReview
      {preview}
      title={`Exclude “${selected.item.content.slice(0, 80)}${selected.item.content.length > 80 ? '…' : ''}”?`}
      busy={applying}
      error={applyError}
      onconfirm={() => void confirm()}
      oncancel={cancelExclusion}
    />
  {/if}

  <ExclusionOutcome {confirmation} {undoResult} {undoBusy} {undoError} onundo={() => void undo()} />
  <p class="memory-footnote">
    Excluding is reversible. It does not delete the stored fact and does not claim to erase conversation history.
  </p>
</section>
