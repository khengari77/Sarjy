import { expect, test, type Page } from '@playwright/test';

const CHATS = [
  { id: 'c1', title: 'Trip to Paris', revision: 0 },
  { id: 'c2', title: 'ميزانية Tripoli', revision: 0 },
  { id: 'c3', title: 'Work ideas', revision: 0 },
];

async function mockEmptyHistory(page: Page) {
  let created = 0;
  await page.route('**/api/v1/conversations', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: [] });
      return;
    }
    created += 1;
    await route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } });
  });
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  return { created: () => created };
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('loading conversations shows an in-panel busy state', async ({ page }) => {
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.fulfill({ json: CHATS });
  });
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.goto('/');
  const sidebar = page.locator('.chat-history');
  await expect(sidebar.locator('.skeleton')).toBeVisible();
  await expect(sidebar).toHaveAttribute('aria-busy', 'true');
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible();
  await expect(sidebar).toHaveAttribute('aria-busy', 'false');
});

test('a list failure retries inside the sidebar without touching the orb', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
      return;
    }
    await route.fulfill({ json: CHATS });
  });
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Conversations' });
  await expect(sidebar.getByText('Could not load conversations.')).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'idle');
  await sidebar.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'idle');
  expect(calls).toBe(2);
});

test('starting from the empty state creates exactly one conversation', async ({ page }) => {
  const mocked = await mockEmptyHistory(page);
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', (route) => {
    sessionPosts += 1;
    return route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Start a conversation' }).click();
  await expect.poll(() => mocked.created()).toBe(1);
  expect(sessionPosts).toBe(0);
});
