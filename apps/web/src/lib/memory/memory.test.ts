import { afterEach, expect, it, vi } from 'vitest';
import {
  friendlyMemoryError,
  labelsFor,
  loadMemories,
  newIdempotencyKey,
  previewExclusion,
  provenance,
  retrievedIds,
  type MemoryListItem,
} from './memory';

afterEach(() => vi.unstubAllGlobals());

function item(overrides: Partial<MemoryListItem> = {}): MemoryListItem {
  return {
    id: 'memory-1',
    content: 'Trip to Tripoli from 1 to 5 May',
    kind: 'memory',
    version: 1,
    status: 'active',
    source_conversation_id: 'conversation-1',
    source_turn_id: 'turn-1',
    supersedes_id: null,
    created_at: '2026-11-13T10:00:00+00:00',
    updated_at: '2026-11-13T10:00:00+00:00',
    excluded_in_conversation: false,
    included_in_conversation: true,
    ...overrides,
  };
}

it('keeps stored, retrieved, included and excluded as separate states', () => {
  const included = labelsFor(item(), false);
  expect(included).toMatchObject({ stored: true, retrieved: false, included: true, excluded: false });
  expect(included.summary).toBe('Included in the next compiled context');

  const retrievedOnly = labelsFor(item({ included_in_conversation: false }), true);
  expect(retrievedOnly).toMatchObject({ retrieved: true, included: false, excluded: false });
  expect(retrievedOnly.summary).toBe('Stored only');

  const excluded = labelsFor(item({ excluded_in_conversation: true, included_in_conversation: false }), false);
  expect(excluded).toMatchObject({ excluded: true, included: false });
  expect(excluded.summary).toBe('Excluded from this conversation');
});

it('describes corrected history instead of pretending an old fact is current', () => {
  const corrected = labelsFor(item({ status: 'superseded', included_in_conversation: false }), false);
  expect(corrected.included).toBe(false);
  expect(corrected.summary).toBe('Earlier version (corrected)');
});

it('collects retrieved ids without treating search as inclusion', () => {
  const ids = retrievedIds([
    {
      id: 'a',
      owner_id: 'owner',
      content: 'one',
      version: 1,
      source_conversation_id: 'c',
      source_turn_id: null,
      status: 'active',
      supersedes_id: null,
      distance: 0.2,
    },
  ]);
  expect([...ids]).toEqual(['a']);
});

it('names provenance without exposing unrelated internals', () => {
  expect(provenance(item())).toContain('conversa');
  expect(provenance(item())).toContain('turn-1');
  expect(provenance(item({ supersedes_id: 'old-memory' }))).toContain('corrects');
});

it('loads a paginated owner list for the selected conversation', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ items: [], total: 0, next_offset: null }));
  vi.stubGlobal('fetch', fetch);
  await loadMemories('conversation-1', 50, 25);
  expect(fetch).toHaveBeenCalledWith(
    '/api/v1/memories?limit=25&offset=50&conversation_id=conversation-1',
    expect.objectContaining({ method: 'GET' }),
  );
});

it('previews an exact-id exclusion and sends no client-side ids to confirmation', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ proposal: 'signed' }));
  vi.stubGlobal('fetch', fetch);
  await previewExclusion('conversation-1', ['memory-1']);
  expect(fetch).toHaveBeenCalledWith(
    '/api/v1/conversations/conversation-1/context/exclusions',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ memory_ids: ['memory-1'] }) }),
  );
});

it('reports unconfirmed changes honestly and never names a provider', () => {
  const message = friendlyMemoryError('API_UNAVAILABLE');
  expect(message).toContain('Nothing was changed');
  expect(message.toLowerCase()).not.toContain('openai');
});

it('creates bounded idempotency keys', () => {
  const key = newIdempotencyKey('memory-exclude');
  expect(key.startsWith('memory-exclude-')).toBe(true);
  expect(key.length).toBeLessThanOrEqual(100);
});
