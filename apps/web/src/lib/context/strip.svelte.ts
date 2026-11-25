import {
  friendlyBlocksError,
  loadContextBlocks,
  memoryChips,
  type ContextBlocks,
  type ContextBudget,
  type ContextChip,
  type FetchLike,
} from './context';

export interface ContextStripOptions {
  conversationId: () => string | null;
  /** Changes when a turn finalises (the memory explorer's refresh signal). */
  revision: () => string;
  fetch?: FetchLike;
}

/**
 * Fetch lifecycle for the "In Sarjy's context" strip.
 *
 * Loaded blocks remember the conversation they were fetched for, so switching
 * conversations drops the previous chips immediately and a late response for a
 * conversation that is no longer selected is discarded.
 */
export class ContextStripState {
  data = $state<ContextBlocks | null>(null);
  loading = $state(false);
  error = $state('');
  #loadedFor = $state<string | null>(null);
  #lastKey = '';
  #generation = 0;
  #nonce = 0;
  #conversationId: () => string | null;
  #revision: () => string;
  #fetch: FetchLike | undefined;

  readonly chips: ContextChip[];
  readonly budget: ContextBudget | undefined;

  constructor(options: ContextStripOptions) {
    this.#conversationId = options.conversationId;
    this.#revision = options.revision;
    this.#fetch = options.fetch;
    this.chips = $derived(this.data && this.#loadedFor === this.#conversationId() ? memoryChips(this.data.blocks) : []);
    this.budget = $derived(this.data && this.#loadedFor === this.#conversationId() ? this.data.budget : undefined);

    $effect(() => {
      void this.refresh();
    });
  }

  retry(): Promise<void> {
    this.#nonce += 1;
    return this.refresh();
  }

  async refresh(): Promise<void> {
    const conversation = this.#conversationId();
    const key = `${conversation ?? ''}:${this.#revision()}:${this.#nonce}`;
    if (key === this.#lastKey) return;
    this.#lastKey = key;
    const request = ++this.#generation;
    if (!conversation) {
      this.data = null;
      this.#loadedFor = null;
      this.loading = false;
      this.error = '';
      return;
    }
    // A revision refresh may keep same-conversation data visible; a switch never can.
    if (this.#loadedFor !== conversation) {
      this.data = null;
      this.#loadedFor = null;
    }
    this.loading = true;
    this.error = '';
    try {
      const result = await loadContextBlocks(conversation, this.#fetch ?? fetch);
      if (request !== this.#generation || this.#conversationId() !== conversation) return;
      this.data = result;
      this.#loadedFor = conversation;
      this.loading = false;
    } catch {
      if (request !== this.#generation || this.#conversationId() !== conversation) return;
      this.data = null;
      this.#loadedFor = null;
      this.loading = false;
      this.error = friendlyBlocksError();
    }
  }
}

export function createContextStrip(options: ContextStripOptions): ContextStripState {
  return new ContextStripState(options);
}
