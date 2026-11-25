import { errorCode } from '../voice/errors';
import { friendlyReceiptError, isContextNotFound, loadTurnContext, type FetchLike, type TurnContext } from './context';

export type ReceiptStatus = 'idle' | 'loading' | 'ready' | 'missing' | 'error';

export interface ReceiptEntry {
  status: ReceiptStatus;
  data?: TurnContext;
  message?: string;
}

export interface ReceiptOptions {
  conversationId: () => string | null;
  fetch?: FetchLike;
}

const IDLE: ReceiptEntry = { status: 'idle' };

/**
 * One lazy receipt per reply. Opening a turn fetches once; a ready, missing or
 * in-flight turn is served from the cache. A failed request can be retried.
 */
export class TurnContextReceipts {
  entries = $state<Record<string, ReceiptEntry>>({});

  #conversationId: () => string | null;
  #fetch: FetchLike | undefined;

  constructor(options: ReceiptOptions) {
    this.#conversationId = options.conversationId;
    this.#fetch = options.fetch;
  }

  #key(turnId: string): string {
    return `${this.#conversationId() ?? ''}:${turnId}`;
  }

  entry(turnId: string): ReceiptEntry {
    return this.entries[this.#key(turnId)] ?? IDLE;
  }

  async open(turnId: string): Promise<void> {
    const conversationId = this.#conversationId();
    if (!conversationId) return;
    const key = `${conversationId}:${turnId}`;
    const current = this.entries[key];
    if (current && current.status !== 'idle' && current.status !== 'error') return;
    this.entries = { ...this.entries, [key]: { status: 'loading' } };
    try {
      const data = await loadTurnContext(conversationId, turnId, this.#fetch ?? fetch);
      if (this.#conversationId() !== conversationId) return;
      this.entries = { ...this.entries, [key]: { status: 'ready', data } };
    } catch (error) {
      if (this.#conversationId() !== conversationId) return;
      if (isContextNotFound(error)) {
        this.entries = {
          ...this.entries,
          [key]: { status: 'missing', message: 'No context was recorded for this reply.' },
        };
        return;
      }
      this.entries = {
        ...this.entries,
        [key]: { status: 'error', message: `${friendlyReceiptError()} (${errorCode(error)})` },
      };
    }
  }
}

export function createTurnContextReceipts(options: ReceiptOptions): TurnContextReceipts {
  return new TurnContextReceipts(options);
}
