import { errorCode } from '../voice/errors';
import {
  changeVisibility,
  friendlyListError,
  friendlyMemoryError,
  labelsFor,
  loadMemories,
  matchesOutsideLoaded,
  retrievedIds,
  searchMemories,
  visibilityFailureText,
  type MemoryListItem,
  type MemoryRow,
  type MemorySearchResult,
  type VisibilityAction,
} from './memory';

export interface MemoryExplorerOptions {
  /** The conversation the panel is scoped to; `null` lists owner facts only. */
  conversationId: () => string | null;
  /** True while the Memories tab is visible. */
  active: () => boolean;
  /** Changes when a turn finalises (memory capture, voice hide/undo) or the chat changes. */
  revision: () => string;
}

interface Page {
  items: MemoryListItem[];
  total: number;
  nextOffset: number | null;
}

const EMPTY: Page = { items: [], total: 0, nextOffset: null };

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  return Object.fromEntries(Object.entries(record).filter(([name]) => name !== key));
}

/**
 * Fetch and visibility lifecycle for the memory explorer.
 *
 * Two lists exist: active memories and hidden ones. Hide, restore and delete
 * are server-confirmed (no optimistic update); a failed call leaves the row
 * untouched and records a short per-row message. Every request captures the
 * scope it was issued for and drops its result if the scope moved on.
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
  /** Memory ids with a request in flight (buttons disabled while set). */
  busy = $state<Record<string, boolean>>({});
  /** Short inline failure per memory id. */
  rowErrors = $state<Record<string, string>>({});

  readonly rows: MemoryRow[] = $derived.by(() => {
    const retrieved = retrievedIds(this.searchResults);
    return this.items.map((item) => ({ item, labels: labelsFor(item, retrieved.has(item.id)) }));
  });

  readonly outsideSearchMatches: number = $derived(
    this.searchRan ? matchesOutsideLoaded(this.searchResults, this.items) : 0,
  );

  #scope: () => string | null;
  #active: () => boolean;
  #revision: () => string;
  #loadedFor: string | null | undefined = undefined;
  #lastSignal: string | null = null;
  #inFlight = new Set<string>();
  #generation = 0;

  constructor(options: MemoryExplorerOptions) {
    this.#scope = options.conversationId;
    this.#active = options.active;
    this.#revision = options.revision;

    $effect(() => {
      const scope = this.#scope();
      if (this.#loadedFor === scope) return;
      this.#loadedFor = scope;
      this.#resetTransient();
      void this.reload();
    });

    // A panel can sit mounted in a hidden tab while a voice turn saves, hides
    // or restores facts. Activation and finalised-turn revisions both refresh.
    $effect(() => {
      const signal = `${this.#active()}:${this.#revision()}`;
      this.#lastSignal ??= signal;
      if (signal === this.#lastSignal) return;
      this.#lastSignal = signal;
      if (this.#active()) void this.refresh();
    });
  }

  #resetTransient() {
    this.searchResults = [];
    this.searchRan = false;
    this.searchError = '';
    this.rowErrors = {};
    this.#generation += 1;
  }

  async #fetch(requested: string | null, offset: number, limit = 50): Promise<Page> {
    // Hidden rows stay in the list (status "hidden"); deleted rows never come back.
    const page = await loadMemories(requested, offset, limit);
    return { items: page.items, total: page.total, nextOffset: page.next_offset };
  }

  /** Initial or scope-change load: shows the loading state. */
  async reload() {
    this.loading = true;
    await this.#refresh(true);
  }

  /** Refresh in place without flashing the loading state. */
  async refresh() {
    await this.#refresh(false);
  }

  async #refresh(initial: boolean) {
    const requested = this.#scope();
    const generation = ++this.#generation;
    this.listError = '';
    try {
      // Keep as many already-loaded rows as one request allows.
      const page = await this.#fetch(requested, 0, Math.min(100, Math.max(50, this.items.length)));
      if (requested !== this.#scope() || generation !== this.#generation) return;
      this.#setActive(page);
    } catch (error) {
      if (requested !== this.#scope() || generation !== this.#generation) return;
      if (initial) this.#setActive(EMPTY);
      this.listError = friendlyListError(errorCode(error));
    }
    this.loading = false;
  }

  #setActive(page: Page) {
    this.items = page.items;
    this.total = page.total;
    this.nextOffset = page.nextOffset;
  }

  async loadMore() {
    const offset = this.nextOffset;
    if (offset === null) return;
    const requested = this.#scope();
    const generation = this.#generation;
    this.loadingMore = true;
    this.listError = '';
    try {
      const page = await this.#fetch(requested, offset);
      if (requested !== this.#scope() || generation !== this.#generation) return;
      const seen = new Set(this.items.map((item) => item.id));
      this.#setActive({ ...page, items: [...this.items, ...page.items.filter((item) => !seen.has(item.id))] });
    } catch (error) {
      if (requested !== this.#scope()) return;
      this.listError = friendlyListError(errorCode(error));
    } finally {
      this.loadingMore = false;
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

  hide(id: string) {
    return this.#change(id, 'hide');
  }

  restore(id: string) {
    return this.#change(id, 'unhide');
  }

  /** Permanent. Only call after the confirmation dialog. Resolves true on success. */
  remove(id: string) {
    return this.#change(id, 'delete');
  }

  /** The server confirmed; mirror it in place so a failed refresh cannot show a stale row. */
  #applyLocally(id: string, action: VisibilityAction) {
    if (action === 'delete') {
      if (this.items.some((item) => item.id === id)) this.total = Math.max(0, this.total - 1);
      this.items = this.items.filter((item) => item.id !== id);
      return;
    }
    const status = action === 'hide' ? 'hidden' : 'active';
    this.items = this.items.map((item) => (item.id === id ? { ...item, status } : item));
  }

  async #change(id: string, action: VisibilityAction): Promise<boolean> {
    // The Set is synchronous, so a double-click cannot slip past the
    // reactive `busy` flag before Svelte re-renders the disabled button.
    if (this.#inFlight.has(id)) return false;
    this.#inFlight.add(id);
    this.busy = { ...this.busy, [id]: true };
    this.rowErrors = without(this.rowErrors, id);
    try {
      await changeVisibility(id, action);
      this.#applyLocally(id, action);
      this.searchResults = this.searchResults.filter((result) => result.id !== id);
      await this.refresh();
      return true;
    } catch (error) {
      this.rowErrors = { ...this.rowErrors, [id]: visibilityFailureText(action, error) };
      return false;
    } finally {
      this.#inFlight.delete(id);
      this.busy = without(this.busy, id);
    }
  }
}

export function createMemoryExplorer(options: MemoryExplorerOptions): MemoryExplorer {
  return new MemoryExplorer(options);
}
