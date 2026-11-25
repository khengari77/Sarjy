import { describe, expect, it, vi } from 'vitest';
import type { ContextBlock } from './context';
import { createContextStrip } from './strip.svelte';

function block(overrides: Partial<ContextBlock> = {}): ContextBlock {
  return {
    id: 'memory-1',
    kind: 'memory',
    role: 'memory',
    text: 'Trip to Tripoli',
    truncated: false,
    estimated_tokens: 4,
    state: 'included',
    reason: null,
    ...overrides,
  };
}

function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe('context strip conversation switching', () => {
  it('drops the previous conversation chips and discards a late response for it', async () => {
    let conversation = 'conversation-a';
    const pending: ReturnType<typeof deferred>[] = [];
    const fetch = vi.fn(() => {
      const gate = deferred();
      pending.push(gate);
      return gate.promise;
    });
    const strip = createContextStrip({ conversationId: () => conversation, revision: () => '', fetch });

    const loadingA = strip.refresh();
    expect(strip.loading).toBe(true);
    expect(strip.chips).toEqual([]);

    conversation = 'conversation-b';
    const loadingB = strip.refresh();
    expect(strip.data).toBeNull();
    expect(strip.chips).toEqual([]);

    pending[0].resolve(Response.json({ blocks: [block({ id: 'a', text: 'A only' })] }));
    await loadingA;
    expect(strip.data).toBeNull();
    expect(strip.chips.map((chip) => chip.id)).toEqual([]);

    pending[1].resolve(Response.json({ blocks: [block({ id: 'b', text: 'B only' })] }));
    await loadingB;
    expect(strip.chips.map((chip) => chip.id)).toEqual(['b']);
    expect(strip.chips[0].text).toBe('B only');
  });

  it('clears loaded chips as soon as the conversation changes', async () => {
    let conversation = 'conversation-a';
    const pending: ReturnType<typeof deferred>[] = [];
    const fetch = vi.fn(() => {
      const gate = deferred();
      pending.push(gate);
      return gate.promise;
    });
    const strip = createContextStrip({ conversationId: () => conversation, revision: () => '', fetch });

    const loadingA = strip.refresh();
    pending[0].resolve(Response.json({ blocks: [block({ id: 'a' })] }));
    await loadingA;
    expect(strip.chips.map((chip) => chip.id)).toEqual(['a']);

    conversation = 'conversation-b';
    void strip.refresh();
    expect(strip.data).toBeNull();
    expect(strip.chips).toEqual([]);
  });
});
