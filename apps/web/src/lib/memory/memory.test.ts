import { afterEach, expect, it, vi } from 'vitest';
import {
  friendlyListError,
  friendlyMemoryError,
  labelsFor,
  loadMemories,
  mutationFailure,
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
    candidate_eligible: true,
    included_in_conversation: true,
    ...overrides,
  };
}

it('calls a next-context fact a candidate, never an included fact', () => {
  const candidate = labelsFor(item(), false);
  expect(candidate).toMatchObject({
    stored: true,
    retrieved: false,
    candidate: true,
    eligible: true,
    excluded: false,
  });
  expect(candidate.summary).toBe('Candidate for the next compiled context');
  // The compiler can still trim the prompt to its character budget; the panel
  // must never promise the model received the fact.
  expect(candidate.summary).not.toContain('Included in the next');

  const retrievedOnly = labelsFor(item({ included_in_conversation: false }), true);
  expect(retrievedOnly).toMatchObject({ retrieved: true, candidate: false, eligible: true, excluded: false });
  expect(retrievedOnly.summary).toBe('Eligible, but outside the current context candidates');

  const storedOnly = labelsFor(item({ candidate_eligible: false, included_in_conversation: false }), false);
  expect(storedOnly.summary).toBe('Stored only');

  const excluded = labelsFor(item({ excluded_in_conversation: true, included_in_conversation: false }), false);
  expect(excluded).toMatchObject({ excluded: true, candidate: false });
  expect(excluded.summary).toBe('Excluded from this conversation');
});

it('treats an excluded correction lineage descendant as excluded, not a candidate', () => {
  // The server flags the descendant through exclusion lineage; a stale or
  // hand-built row that only carries the exclusion bit must still not claim
  // candidacy.
  const descendant = labelsFor(
    item({
      id: 'memory-2',
      supersedes_id: 'memory-1',
      excluded_in_conversation: true,
      candidate_eligible: false,
      included_in_conversation: false,
    }),
    false,
  );
  expect(descendant).toMatchObject({ excluded: true, candidate: false, eligible: false });
  expect(descendant.summary).toBe('Excluded from this conversation');
});

it('describes corrected history instead of pretending an old fact is current', () => {
  const corrected = labelsFor(
    item({ status: 'superseded', candidate_eligible: false, included_in_conversation: false }),
    false,
  );
  expect(corrected.candidate).toBe(false);
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

it('never claims nothing changed when a mutation outcome is unknown', () => {
  const unknown = mutationFailure(new TypeError('network down'));
  expect(unknown.kind).toBe('unknown');
  expect(unknown.message).toContain('did not confirm whether the change was applied');
  expect(unknown.message).not.toContain('Nothing was changed');
  expect(unknown.message.toLowerCase()).not.toContain('openai');

  const serverError = mutationFailure(new Error('HTTP_503'));
  expect(serverError.kind).toBe('unknown');

  const rejected = mutationFailure(new Error('REVISION_CONFLICT'));
  expect(rejected.kind).toBe('rejected');
  expect(rejected.message).toContain('Nothing was excluded');

  const notFound = mutationFailure(new Error('HTTP_404'));
  expect(notFound.kind).toBe('rejected');
});

it('keeps read failures truthful without promising an outcome', () => {
  expect(friendlyMemoryError('API_UNAVAILABLE').length).toBeGreaterThan(0);
  expect(friendlyMemoryError('API_UNAVAILABLE').toLowerCase()).not.toContain('openai');
});

it('creates bounded idempotency keys', () => {
  const key = newIdempotencyKey('memory-exclude');
  expect(key.startsWith('memory-exclude-')).toBe(true);
  expect(key.length).toBeLessThanOrEqual(100);
});

it('keeps read failures read-shaped so they never contradict a confirmed change', () => {
  for (const code of ['API_UNAVAILABLE', 'HTTP_503', 'API_RESPONSE_INVALID']) {
    const message = friendlyListError(code);
    expect(message).toContain('could not be reloaded');
    expect(message).toContain('Any confirmed change still stands');
    expect(message).not.toContain('Nothing was changed');
  }
  expect(friendlyListError('WEB_AUTH_REQUIRED')).toContain('session expired');
});
