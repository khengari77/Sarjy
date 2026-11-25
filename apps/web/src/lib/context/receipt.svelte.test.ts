import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTurnContextReceipts } from './receipt.svelte';

afterEach(() => vi.unstubAllGlobals());

function payload(turnId = 'turn-1') {
  return {
    turn_id: turnId,
    revision: 1,
    prompt_sha256: 'abcdef123456',
    history_count: 0,
    included: [],
    excluded: [],
    budget: { used_chars: 20, limit_chars: 100 },
    previous_turn_id: null,
    diff: { added: [], removed: [] },
  };
}

function responding(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(Response.json(body, { status }));
}

describe('turn context receipts', () => {
  it('fetches lazily on first open and caches the reply', async () => {
    const fetch = responding(payload());
    const receipts = createTurnContextReceipts({ conversationId: () => 'conversation-1', fetch });

    expect(fetch).not.toHaveBeenCalled();
    expect(receipts.entry('turn-1').status).toBe('idle');

    await receipts.open('turn-1');
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith('/api/v1/conversations/conversation-1/turns/turn-1/context');
    expect(receipts.entry('turn-1')).toMatchObject({ status: 'ready', data: { turn_id: 'turn-1' } });

    await receipts.open('turn-1');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('returns the friendly missing copy for a 404 and caches it', async () => {
    const fetch = responding({ detail: { code: 'TURN_NOT_FOUND' } }, 404);
    const receipts = createTurnContextReceipts({ conversationId: () => 'conversation-1', fetch });

    await receipts.open('turn-9');
    expect(receipts.entry('turn-9')).toEqual({
      status: 'missing',
      message: 'No context was recorded for this reply.',
    });

    await receipts.open('turn-9');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('leaves an error retryable without caching a failure as final', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('not json', { status: 503 }));
    const receipts = createTurnContextReceipts({ conversationId: () => 'conversation-1', fetch });

    await receipts.open('turn-1');
    expect(receipts.entry('turn-1').status).toBe('error');
    expect(receipts.entry('turn-1').message).toContain('could not be loaded');

    fetch.mockResolvedValue(Response.json(payload()));
    await receipts.open('turn-1');
    expect(receipts.entry('turn-1').status).toBe('ready');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('does nothing without a conversation', async () => {
    const fetch = responding(payload());
    const receipts = createTurnContextReceipts({ conversationId: () => null, fetch });
    await receipts.open('turn-1');
    expect(fetch).not.toHaveBeenCalled();
  });
});
