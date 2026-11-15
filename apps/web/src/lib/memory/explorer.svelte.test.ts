import { afterEach, expect, it, vi } from 'vitest';
import { createMemoryExplorer } from './explorer.svelte';

afterEach(() => vi.unstubAllGlobals());

const fact = (id: string, status: string) => ({
  id,
  content: id,
  kind: 'memory',
  version: 1,
  status,
  source_conversation_id: 'c1',
  source_turn_id: null,
  supersedes_id: null,
  created_at: '2026-11-01T10:00:00Z',
  updated_at: '2026-11-01T10:00:00Z',
});

// Effects are not run outside a component, so these cover the methods the
// effects call (refresh) and the visibility lifecycle; the revision wiring is
// covered by the browser spec.
function setup(server: { items: ReturnType<typeof fact>[]; failWrites: boolean }) {
  const writes: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST' || init?.method === 'DELETE') {
        writes.push(`${init.method} ${url}`);
        if (server.failWrites) return new Response(JSON.stringify({ detail: {} }), { status: 503 });
        return new Response(JSON.stringify({ id: 'a', status: 'hidden', changed: true }));
      }
      return new Response(JSON.stringify({ items: server.items, total: server.items.length, next_offset: null }));
    }),
  );
  return { explorer: createMemoryExplorer({ conversationId: () => 'c1', active: () => true, revision: () => '' }), writes };
}

it('refresh picks up a voice hide and a voice undo in place', async () => {
  const server = { items: [fact('a', 'active')], failWrites: false };
  const { explorer } = setup(server);
  await explorer.refresh();
  expect(explorer.items[0].status).toBe('active');
  server.items = [fact('a', 'hidden')];
  await explorer.refresh();
  expect(explorer.items.map((item) => [item.id, item.status])).toEqual([['a', 'hidden']]);
  server.items = [fact('a', 'active')];
  await explorer.refresh();
  expect(explorer.items[0].status).toBe('active');
});

it('hides in place after the server confirms, and a double call sends one request', async () => {
  const server = { items: [fact('a', 'active'), fact('b', 'active')], failWrites: false };
  const { explorer, writes } = setup(server);
  await explorer.refresh();
  server.items = [fact('a', 'hidden'), fact('b', 'active')];
  await Promise.all([explorer.hide('a'), explorer.hide('a')]);
  expect(writes).toEqual(['POST /api/v1/memories/a/hide']);
  expect(explorer.items.map((item) => [item.id, item.status])).toEqual([
    ['a', 'hidden'],
    ['b', 'active'],
  ]);
});

it('keeps the row unchanged and records a short error when the call fails', async () => {
  const server = { items: [fact('a', 'active')], failWrites: true };
  const { explorer } = setup(server);
  await explorer.refresh();
  expect(await explorer.remove('a')).toBe(false);
  expect(explorer.items).toHaveLength(1);
  expect(explorer.rowErrors.a).toContain("Couldn't delete");
  expect(explorer.busy.a).toBeUndefined();
});
