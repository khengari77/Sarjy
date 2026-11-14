import { errorCode } from '../voice/errors';
import {
  confirmExclusion,
  friendlyListError,
  friendlyMemoryError,
  labelsFor,
  loadMemories,
  mutationFailure,
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

export interface MemoryExplorerOptions {
  /** The conversation the panel is scoped to; `null` lists owner facts only. */
  conversationId: () => string | null;
  /** True while the Memories tab is visible. */
  active: () => boolean;
  /** Changes when a turn finalises or the chat changes. */
  revision: () => string;
}

/**
 * Fetch/confirm/undo lifecycle for the memory explorer.
 *
 * Rendering stays in `MemoryPanel.svelte`; this module owns the async
 * orchestration so the component stays a view. Every request captures the
 * conversation it was issued for and drops its result if the scope moved on.
 */
export class MemoryExplorer {
  items = $state<MemoryListItem[]>([]);
  total = $state(0);
  nextOffset = $state<number | null>(null);
  loading = $state(true);
  loadingMore = $state(false);
  listError = $state('');
  query = $state('');
  searching = $state(false);
  searchError = $state('');
  searchRan = $state(false);
  searchResults = $state<MemorySearchResult[]>([]);
  selected = $state<MemoryRow | null>(null);
  preview = $state<ContextProposal | null>(null);
  previewLoading = $state(false);
  previewError = $state('');
  applying = $state(false);
  applyError = $state('');
  confirmation = $state<ContextConfirmation | null>(null);
  undoBusy = $state(false);
  undoError = $state('');
  undoResult = $state<ContextUndoResult | null>(null);

  readonly rows: MemoryRow[] = $derived.by(() => {
    const retrieved = retrievedIds(this.searchResults);
    return this.items.map((item) => ({ item, labels: labelsFor(item, retrieved.has(item.id)) }));
  });

  readonly outsideSearchMatches: number = $derived(
    this.searchRan
      ? this.searchResults.filter((result) => !this.items.some((item) => item.id === result.id)).length
      : 0,
  );

  #scope: () => string | null;
  #active: () => boolean;
  #revision: () => string;
  #loadedFor: string | null | undefined = undefined;
  #lastSignal: string | null = null;

  constructor(options: MemoryExplorerOptions) {
    this.#scope = options.conversationId;
    this.#active = options.active;
    this.#revision = options.revision;

    // A different conversation is a different list and cancels every pending
    // decision, including busy flags from an abandoned request.
    $effect(() => {
      const scope = this.#scope();
      if (this.#loadedFor === scope) return;
      this.#loadedFor = scope;
      this.#resetTransient();
      void this.reload();
    });

    // A panel can sit mounted in a hidden tab while a voice turn saves facts.
    // Activation and finalised-turn revisions both invalidate the list.
    $effect(() => {
      const signal = `${this.#active()}:${this.#revision()}`;
      this.#lastSignal ??= signal;
      if (signal === this.#lastSignal) return;
      this.#lastSignal = signal;
      if (this.#active()) void this.reload();
    });
  }

  #resetTransient() {
    this.searchResults = [];
    this.searchRan = false;
    this.searchError = '';
    this.preview = null;
    this.confirmation = null;
    this.undoResult = null;
    this.selected = null;
    this.previewError = '';
    this.applyError = '';
    this.undoError = '';
    this.previewLoading = false;
    this.applying = false;
    this.undoBusy = false;
  }

  async reload() {
    const requested = this.#scope();
    this.loading = true;
    this.listError = '';
    try {
      const page = await loadMemories(requested, 0);
      if (requested !== this.#scope()) return;
      this.items = page.items;
      this.total = page.total;
      this.nextOffset = page.next_offset;
    } catch (error) {
      if (requested !== this.#scope()) return;
      this.items = [];
      this.total = 0;
      this.nextOffset = null;
      this.listError = friendlyListError(errorCode(error));
    } finally {
      if (requested === this.#scope()) this.loading = false;
    }
  }

  async loadMore() {
    if (this.nextOffset === null) return;
    const requested = this.#scope();
    this.loadingMore = true;
    this.listError = '';
    try {
      const page = await loadMemories(requested, this.nextOffset);
      if (requested !== this.#scope()) return;
      this.items = [...this.items, ...page.items];
      this.total = page.total;
      this.nextOffset = page.next_offset;
    } catch (error) {
      if (requested !== this.#scope()) return;
      this.listError = friendlyListError(errorCode(error));
    } finally {
      if (requested === this.#scope()) this.loadingMore = false;
    }
  }

  async search(value: string) {
    const requested = this.#scope();
    if (!requested) return;
    this.searching = true;
    this.searchError = '';
    try {
      const results = await searchMemories(requested, value);
      if (requested !== this.#scope()) return;
      this.searchResults = results;
      this.searchRan = true;
    } catch (error) {
      if (requested !== this.#scope()) return;
      this.searchResults = [];
      this.searchRan = false;
      this.searchError = friendlyMemoryError(errorCode(error));
    } finally {
      if (requested === this.#scope()) this.searching = false;
    }
  }

  async beginExclusion(row: MemoryRow) {
    const requested = this.#scope();
    if (!requested) return;
    this.selected = row;
    this.preview = null;
    this.previewError = '';
    this.applyError = '';
    this.confirmation = null;
    this.undoResult = null;
    this.undoError = '';
    this.previewLoading = true;
    try {
      const proposal = await previewExclusion(requested, [row.item.id]);
      if (requested !== this.#scope()) return;
      this.preview = proposal;
    } catch (error) {
      if (requested !== this.#scope()) return;
      this.previewError = friendlyMemoryError(errorCode(error));
    } finally {
      if (requested === this.#scope()) this.previewLoading = false;
    }
  }

  cancelExclusion() {
    this.selected = null;
    this.preview = null;
    this.previewError = '';
    this.applyError = '';
  }

  async confirm() {
    const requested = this.#scope();
    if (!requested || !this.preview) return;
    this.applying = true;
    this.applyError = '';
    try {
      const confirmation = await confirmExclusion(
        requested,
        this.preview.proposal,
        this.preview.revision,
        newIdempotencyKey('memory-exclude'),
      );
      if (requested !== this.#scope()) return;
      this.confirmation = confirmation;
      this.preview = null;
      this.selected = null;
      await this.reload();
    } catch (error) {
      if (requested !== this.#scope()) return;
      this.applyError = mutationFailure(error).message;
    } finally {
      if (requested === this.#scope()) this.applying = false;
    }
  }

  async undo() {
    const requested = this.#scope();
    const confirmation = this.confirmation;
    if (!requested || !confirmation) return;
    this.undoBusy = true;
    this.undoError = '';
    try {
      const result = await undoExclusion(
        requested,
        confirmation.operation_id,
        confirmation.revision_after,
        newIdempotencyKey('memory-undo'),
      );
      if (requested !== this.#scope()) return;
      this.undoResult = result;
      this.confirmation = null;
      await this.reload();
    } catch (error) {
      if (requested !== this.#scope()) return;
      this.undoError = mutationFailure(error).message;
    } finally {
      if (requested === this.#scope()) this.undoBusy = false;
    }
  }
}

export function createMemoryExplorer(options: MemoryExplorerOptions): MemoryExplorer {
  return new MemoryExplorer(options);
}
