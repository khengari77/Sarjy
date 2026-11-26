import { describe, expect, it, vi } from 'vitest';
import {
  diffLabel,
  isContextNotFound,
  loadContextBlocks,
  loadTurnContext,
  memoryChips,
  meterValues,
  type ContextBlock,
} from './context';

const budget = { used_chars: 120, limit_chars: 400 };

function turnPayload(overrides: Record<string, unknown> = {}) {
  return {
    turn_id: 'turn-1',
    revision: 3,
    prompt_sha256: 'abcdef123456',
    history_count: 2,
    included: [{ id: 'memory-1', kind: 'memory', text: 'Trip to Tripoli', trust: 'data', withheld: false }],
    excluded: [{ id: 'memory-2', reason: 'conversation_exclusion' }],
    budget,
    previous_turn_id: 'turn-0',
    diff: { added: ['memory-1'], removed: ['memory-2'] },
    ...overrides,
  };
}

function responding(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(Response.json(body, { status }));
}

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

describe('turn context fetch', () => {
  it('parses a valid receipt and drops any text attached to an excluded entry', async () => {
    const payload = turnPayload({
      excluded: [{ id: 'memory-2', reason: 'conversation_exclusion', text: 'secret content' }],
    });
    const fetch = responding(payload);
    const result = await loadTurnContext('conversation-1', 'turn-1', fetch);

    expect(fetch).toHaveBeenCalledWith('/api/v1/conversations/conversation-1/turns/turn-1/context');
    expect(result.turn_id).toBe('turn-1');
    expect(result.included[0]).toMatchObject({ id: 'memory-1', kind: 'memory', trust: 'data' });
    expect(result.excluded).toEqual([{ id: 'memory-2', reason: 'conversation_exclusion' }]);
    expect(JSON.stringify(result)).not.toContain('secret content');
  });

  it('rejects a malformed included kind instead of rendering it', async () => {
    const fetch = responding(
      turnPayload({ included: [{ id: 'x', kind: 'system', text: 'instructions', trust: 'data' }] }),
    );
    await expect(loadTurnContext('conversation-1', 'turn-1', fetch)).rejects.toThrow(
      'CONTEXT_INVALID:context.included.0.kind',
    );
  });

  it('rejects a receipt without a budget', async () => {
    const fetch = responding(turnPayload({ budget: undefined }));
    await expect(loadTurnContext('conversation-1', 'turn-1', fetch)).rejects.toThrow('CONTEXT_INVALID:context.budget');
  });

  it('recognises a 404 even when the server supplies its own code', async () => {
    const fetch = responding({ detail: { code: 'TURN_NOT_FOUND' } }, 404);
    const error = await loadTurnContext('conversation-1', 'turn-9', fetch).catch((caught: unknown) => caught);
    expect(isContextNotFound(error)).toBe(true);
    expect((error as Error).message).toBe('TURN_NOT_FOUND');
  });
});

describe('context blocks fetch', () => {
  it('loads the compiler blocks and keeps an optional budget', async () => {
    const fetch = responding({ blocks: [block()], budget });
    const result = await loadContextBlocks('conversation-1', fetch);
    expect(fetch).toHaveBeenCalledWith('/api/v1/conversations/conversation-1/context/blocks?limit=100');
    expect(result.blocks).toHaveLength(1);
    expect(result.budget).toEqual(budget);
  });

  it('rejects malformed blocks', async () => {
    const fetch = responding({ blocks: [{ id: 'x', kind: 'memory' }] });
    await expect(loadContextBlocks('conversation-1', fetch)).rejects.toThrow('CONTEXT_INVALID:blocks.blocks.0.role');
  });
});

describe('memory chips', () => {
  it('names included, hidden, forgotten and over-budget memory blocks', () => {
    const chips = memoryChips([
      block({ id: 'a', state: 'included' }),
      block({ id: 'b', state: 'blocked', reason: 'hidden' }),
      block({ id: 'c', state: 'excluded', reason: 'conversation_exclusion' }),
      block({ id: 'd', state: 'not_sent', reason: 'budget' }),
      block({ id: 'e', kind: 'turn', state: 'included' }),
    ]);

    expect(chips.map((chip) => [chip.id, chip.state, chip.label])).toEqual([
      ['a', 'included', 'Included'],
      ['b', 'hidden', 'Hidden by you'],
      ['c', 'forgotten', 'Forgotten in this chat'],
      ['d', 'over_budget', 'Over budget'],
    ]);
    expect(chips[3].reason).toBe('Over the character budget');
    expect(chips[3].accessible).toContain('Over the character budget');
    expect(chips[1].accessible).toContain('Hidden by you');
  });
});

describe('budget meter', () => {
  it('clamps a fully used budget and reports the raw overage', () => {
    expect(meterValues({ used_chars: 250, limit_chars: 200 })).toEqual({
      used: 250,
      limit: 200,
      now: 200,
      min: 0,
      max: 200,
      percent: 100,
      over: true,
    });
    expect(meterValues({ used_chars: 50, limit_chars: 200 }).percent).toBe(25);
  });

  it('does not divide by a zero limit', () => {
    expect(meterValues({ used_chars: 10, limit_chars: 0 })).toMatchObject({ now: 0, max: 1, percent: 0, over: true });
  });
});

describe('diff labels', () => {
  const included = [{ id: 'memory-1', kind: 'memory' as const, text: 'Trip to Tripoli', trust: 'data' as const, withheld: false }];
  const excluded = [{ id: 'memory-2', reason: 'conversation_exclusion' }];

  it('resolves known ids to receipt labels and unknown ids to a short id', () => {
    expect(diffLabel('memory-1', included, excluded)).toContain('Trip to Tripoli');
    expect(diffLabel('memory-2', included, excluded)).toContain('Forgotten in this chat');
    expect(diffLabel('abcdefghijklmnop', included, excluded)).toBe('abcdefgh…');
  });
});
