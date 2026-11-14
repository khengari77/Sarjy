import type { Page } from '@playwright/test';

// UI-level cover for the transparent memory explorer mounted in the #120 shell
// drawer (#43/#122). The API contract is unit- and route-tested elsewhere; this
// spec checks what a reviewer can actually do in the browser:
//   1. the panel is reachable from the drawer in both themes,
//   2. the stored list is conversation-scoped and shows the inclusion projection,
//   3. "Exclude…" only previews, and nothing changes until Confirm,
//   4. Confirm applies the server-returned diff and Undo restores.

export const FACTS = [
  {
    id: 'mem-included',
    content: 'Prefers tea over coffee',
    kind: 'memory',
    version: 2,
    status: 'active',
    source_conversation_id: 'c1',
    source_turn_id: 'turn-1',
    supersedes_id: null,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-02T10:00:00Z',
    excluded_in_conversation: false,
    candidate_eligible: true,
    included_in_conversation: true,
  },
  {
    id: 'mem-old-version',
    content: 'Lives in Riyadh (corrected)',
    kind: 'memory',
    version: 1,
    status: 'superseded',
    source_conversation_id: 'c1',
    source_turn_id: 'turn-0',
    supersedes_id: null,
    created_at: '2026-10-30T09:00:00Z',
    updated_at: '2026-10-30T09:00:00Z',
    excluded_in_conversation: false,
    candidate_eligible: false,
    included_in_conversation: false,
  },
  {
    id: 'mem-lineage-descendant',
    content: 'Hotel in Paris, 2 nights',
    kind: 'memory',
    version: 2,
    status: 'active',
    source_conversation_id: 'c1',
    source_turn_id: 'turn-3',
    supersedes_id: 'mem-excluded',
    created_at: '2026-11-03T09:00:00Z',
    updated_at: '2026-11-03T09:00:00Z',
    excluded_in_conversation: true,
    candidate_eligible: false,
    included_in_conversation: false,
  },
  {
    id: 'mem-excluded',
    content: 'Lives in Riyadh',
    kind: 'memory',
    version: 1,
    status: 'active',
    source_conversation_id: 'c1',
    source_turn_id: 'turn-2',
    supersedes_id: null,
    created_at: '2026-11-01T09:00:00Z',
    updated_at: '2026-11-01T09:00:00Z',
    excluded_in_conversation: true,
    candidate_eligible: false,
    included_in_conversation: false,
  },
];

export const DIFF = {
  added: [],
  removed: ['mem-included'],
  kept: ['mem-excluded'],
  revision_before: 3,
  revision_after: 4,
};

export interface Calls {
  memories: string[];
  previews: number;
  confirms: Array<Record<string, unknown>>;
  undos: Array<Record<string, unknown>>;
  /** Simulate a save that lands while the panel is mounted but hidden. */
  setFacts(next: typeof FACTS): void;
  /** Make the next list refreshes fail (a mutation result still stands). */
  failList(remaining: number): void;
  /** Control what Undo reports as restored. */
  setUndoRestored(ids: string[]): void;
}

export async function mockApi(page: Page, initialFacts = FACTS): Promise<Calls> {
  const facts = [...initialFacts];
  let listFailures = 0;
  let undoRestored = ['mem-included'];
  const calls: Calls = {
    memories: [],
    previews: 0,
    confirms: [],
    undos: [],
    setFacts: (next) => {
      facts.splice(0, facts.length, ...next);
    },
    failList: (remaining) => {
      listFailures = remaining;
    },
    setUndoRestored: (ids) => {
      undoRestored = ids;
    },
  };

  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: [{ id: 'c1', title: 'Trip to Paris', revision: 0 }] });
    }
    return route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } });
  });
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/voices?*', (route) => route.fulfill({ json: { voices: [], next_page_token: null } }));

  await page.route('**/api/v1/memories?*', (route) => {
    calls.memories.push(route.request().url());
    if (listFailures > 0) {
      listFailures -= 1;
      return route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
    }
    return route.fulfill({ json: { items: facts, total: facts.length, next_offset: null } });
  });

  await page.route('**/api/v1/conversations/c1/context/exclusions', (route) => {
    calls.previews += 1;
    return route.fulfill({
      json: {
        conversation_id: 'c1',
        query: '',
        revision: 3,
        proposal: 'signed-preview-token',
        matches: [
          {
            id: 'mem-included',
            content: 'Prefers tea over coffee',
            source_turn_id: 'turn-1',
            score: 1,
            kind: 'memory',
          },
        ],
        preserved: [],
        ambiguous: [],
        requires_clarification: false,
        diff: DIFF,
      },
    });
  });

  await page.route('**/api/v1/conversations/c1/context/confirm', (route) => {
    calls.confirms.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({
      json: {
        operation_id: 'op-1',
        status: 'applied',
        idempotent: false,
        excluded_ids: ['mem-included'],
        kept_ids: ['mem-excluded'],
        revision_before: 3,
        revision_after: 4,
        diff: DIFF,
      },
    });
  });

  await page.route('**/api/v1/conversations/c1/context/undo', (route) => {
    calls.undos.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({
      json: {
        operation_id: 'op-2',
        restored_operation_id: 'op-1',
        status: 'applied',
        idempotent: false,
        restored_ids: undoRestored,
        revision_before: 4,
        revision_after: 5,
        diff: { added: undoRestored, removed: [], kept: ['mem-excluded'], revision_before: 4, revision_after: 5 },
      },
    });
  });

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
