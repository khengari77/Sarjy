import type { Page, Route } from '@playwright/test';

// UI-level cover for the memory explorer mounted in the shell drawer. Per-memory
// Hide (reversible, no prompt), Restore and Delete (confirmation dialog) call
// the owner-scoped visibility routes; the API itself is route-tested elsewhere.

function fact(id: string, content: string, status = 'active', extra: Record<string, unknown> = {}) {
  return {
    id,
    content,
    kind: 'memory',
    version: 1,
    status,
    source_conversation_id: 'c1',
    source_turn_id: 'turn-1',
    supersedes_id: null,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-02T10:00:00Z',
    excluded_in_conversation: false,
    candidate_eligible: status === 'active',
    included_in_conversation: status === 'active',
    ...extra,
  };
}

export type Fact = ReturnType<typeof fact>;

export const FACTS: Fact[] = [
  fact('mem-tea', 'Prefers tea over coffee'),
  fact('mem-riyadh', 'Lives in Riyadh'),
  fact('mem-paris', 'Hotel in Paris, 2 nights', 'hidden'),
];

export type VisibilityCall = { id: string; method: string; action: 'hide' | 'unhide' | 'delete' };

export interface Calls {
  memories: string[];
  visibility: VisibilityCall[];
  /** Simulate a server-side change (a voice turn hiding or restoring a fact). */
  setFacts(next: Fact[]): void;
  /** Fail the next visibility call(s) with a 503. */
  failVisibility(remaining: number): void;
  /** Hold visibility responses until release() is called. */
  holdVisibility(): { release(): void };
  /** Replace the turns returned for the open conversation (changes the memory revision). */
  setTurns(turns: unknown[]): void;
}

export async function mockApi(page: Page, initialFacts: Fact[] = FACTS): Promise<Calls> {
  let facts = [...initialFacts];
  let visibilityFailures = 0;
  let gate: Promise<void> | null = null;
  let turns: unknown[] = [];
  const calls: Calls = {
    memories: [],
    visibility: [],
    setFacts: (next) => {
      facts = [...next];
    },
    failVisibility: (remaining) => {
      visibilityFailures = remaining;
    },
    holdVisibility: () => {
      let release = () => {};
      gate = new Promise<void>((resolve) => (release = resolve));
      return { release: () => release() };
    },
    setTurns: (next) => {
      turns = next;
    },
  };

  const visibility = async (route: Route, action: VisibilityCall['action']) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[4]);
    calls.visibility.push({ id, method: route.request().method(), action });
    if (gate) await gate;
    if (visibilityFailures > 0) {
      visibilityFailures -= 1;
      return route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
    }
    const status = action === 'hide' ? 'hidden' : action === 'unhide' ? 'active' : 'deleted';
    facts =
      status === 'deleted' ? facts.filter((f) => f.id !== id) : facts.map((f) => (f.id === id ? { ...f, status } : f));
    return route.fulfill({ json: { id, status, changed: true } });
  };

  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: [{ id: 'c1', title: 'Trip to Paris', revision: 0 }] });
    }
    return route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } });
  });
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: turns }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/voices?*', (route) => route.fulfill({ json: { voices: [], next_page_token: null } }));

  await page.route('**/api/v1/memories?*', (route) => {
    calls.memories.push(route.request().url());
    const status = new URL(route.request().url()).searchParams.get('status');
    const items = facts.filter((f) => (status ? f.status === status : f.status !== 'deleted'));
    return route.fulfill({ json: { items, total: items.length, next_offset: null } });
  });
  await page.route(/\/api\/v1\/memories\/[^/]+\/hide$/, (route) => visibility(route, 'hide'));
  await page.route(/\/api\/v1\/memories\/[^/]+\/unhide$/, (route) => visibility(route, 'unhide'));
  await page.route(/\/api\/v1\/memories\/[^/?]+$/, (route) => visibility(route, 'delete'));

  return calls;
}

/** SSR ships static markup; wait for hydrated handlers before clicking. */
export async function waitForShell(page: Page) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const hydrated = await page
      .waitForFunction(
        () =>
          Boolean(document.querySelector('.orb-button')) &&
          document.querySelector('.orb-button')?.getAttribute('data-renderer') !== 'static',
        null,
        { timeout: 12_000 },
      )
      .then(() => true)
      .catch(() => false);
    if (hydrated) return;
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  throw new Error('shell never hydrated');
}

export async function openMemoryTab(page: Page) {
  await page.getByRole('button', { name: 'Show memory and settings' }).click();
  await page.locator('#shell-right').getByRole('tab', { name: 'Memories' }).click();
  return page.locator('#shell-right').getByTestId('memory-panel');
}
