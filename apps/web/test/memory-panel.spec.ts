import { expect, test, type Page } from '@playwright/test';

// UI-level cover for the transparent memory explorer mounted in the #120 shell
// drawer (#43/#122). The API contract is unit- and route-tested elsewhere; this
// spec checks what a reviewer can actually do in the browser:
//   1. the panel is reachable from the drawer in both themes,
//   2. the stored list is conversation-scoped and shows the inclusion projection,
//   3. "Exclude…" only previews, and nothing changes until Confirm,
//   4. Confirm applies the server-returned diff and Undo restores.

const FACTS = [
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
    included_in_conversation: true,
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
    included_in_conversation: false,
  },
];

const DIFF = { added: [], removed: ['mem-included'], kept: ['mem-excluded'], revision_before: 3, revision_after: 4 };

interface Calls {
  memories: string[];
  previews: number;
  confirms: Array<Record<string, unknown>>;
  undos: Array<Record<string, unknown>>;
}

async function mockApi(page: Page): Promise<Calls> {
  const calls: Calls = { memories: [], previews: 0, confirms: [], undos: [] };

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
    return route.fulfill({ json: { items: FACTS, total: FACTS.length, next_offset: null } });
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
        restored_ids: ['mem-included'],
        revision_before: 4,
        revision_after: 5,
        diff: { added: ['mem-included'], removed: [], kept: ['mem-excluded'], revision_before: 4, revision_after: 5 },
      },
    });
  });

  return calls;
}

/** SSR ships static markup; wait for hydrated handlers before clicking. */
async function waitForShell(page: Page) {
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

async function openMemoryTab(page: Page) {
  await page.getByRole('button', { name: 'Show memory and settings' }).click();
  await page.locator('#shell-right').getByRole('tab', { name: 'Memories' }).click();
  return page.locator('#shell-right').getByTestId('memory-panel');
}

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

// Pin the OS scheme so "system" resolves predictably when cycling themes here.
test.use({ colorScheme: 'light' });

test('the memory explorer mounts in the drawer, scopes the list to the conversation and shows inclusion', async ({
  page,
}) => {
  const calls = await mockApi(page);
  await page.addInitScript(() => localStorage.setItem('sarjy-theme', 'dark'));
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  // Selecting a conversation is what gives the panel its conversation scope.
  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  const panel = await openMemoryTab(page);

  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Prefers tea over coffee');
  await expect(panel).toContainText('Lives in Riyadh');
  await expect(panel).toContainText('2 shown of 2 stored facts');

  // The inclusion projection is the acceptance point: one fact is in the next
  // compiled context, one is already excluded from it.
  await expect(panel.locator('[data-included="true"]')).toContainText('Included in the next compiled context');
  await expect(panel.locator('[data-excluded="true"]')).toContainText('Excluded from this conversation');
  await expect(panel.locator('[data-included="true"]')).toContainText('Included');
  await expect(panel.locator('[data-excluded="true"]')).toContainText('Excluded');

  // Owner scoping is enforced by the API; the UI must at least ask for the
  // selected conversation rather than an unscoped dump.
  const scoped = calls.memories.filter((url) => new URL(url).searchParams.get('conversation_id') === 'c1');
  expect(scoped.length).toBeGreaterThan(0);

  // Reachable and legible in both themes without remounting the shell.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(panel).toBeVisible();
  // dark → system, which resolves to light under the pinned colour scheme.
  await page.getByRole('button', { name: /^Theme:/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Prefers tea over coffee');
});

test('excluding previews without writing, then confirms and undoes through the server', async ({ page }) => {
  const calls = await mockApi(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  const panel = await openMemoryTab(page);
  await expect(panel).toContainText('Prefers tea over coffee');

  const includedRow = panel.locator('[data-included="true"]');
  await includedRow.getByRole('button', { name: 'Exclude…' }).click();

  // Preview only: the review exists, says nothing changed, and no confirm was sent.
  const review = panel.getByTestId('exclusion-review');
  await expect(review).toBeVisible();
  await expect(review).toContainText('Preview only — nothing has changed yet');
  await expect(review).toContainText('Will be excluded from this conversation');
  await expect(review).toContainText('Revision 3 → 4');
  expect(calls.previews).toBe(1);
  expect(calls.confirms).toHaveLength(0);
  // The stored row is unchanged while the preview is open.
  await expect(includedRow).toContainText('Included in the next compiled context');
  await expect(panel.getByTestId('exclusion-confirmed')).toHaveCount(0);

  await review.getByRole('button', { name: 'Confirm exclusion' }).click();

  await expect(panel.getByTestId('exclusion-confirmed')).toContainText('Exclusion confirmed');
  await expect(panel.getByTestId('exclusion-confirmed')).toContainText('Applied change');
  expect(calls.confirms).toHaveLength(1);
  expect(calls.confirms[0].proposal).toBe('signed-preview-token');
  expect(calls.confirms[0].expected_revision).toBe(3);
  await expect(panel.getByTestId('exclusion-review')).toHaveCount(0);

  await panel.getByRole('button', { name: 'Undo exclusion' }).click();
  await expect(panel.getByTestId('exclusion-undone')).toContainText('Exclusion undone');
  expect(calls.undos).toHaveLength(1);
  expect(calls.undos[0].operation_id).toBe('op-1');
  expect(calls.undos[0].expected_revision).toBe(4);
});
