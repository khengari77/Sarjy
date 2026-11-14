import { expect, test, type Page } from '@playwright/test';
import { mockConversations, mockSessions, ready } from './support/raceHarness';

const CONTEXT = { conversation_id: 'c1', revision: 3, excluded_ids: ['m2'] };
const OPERATIONS = {
  conversation_id: 'c1',
  revision: 3,
  operations: [
    {
      id: 'op1',
      command_type: 'exclude',
      outcome: 'applied',
      revision_before: 2,
      revision_after: 3,
      idempotency_key: 'should-not-render',
      created_at: '2026-11-14T09:00:00Z',
      diff: { added: [], removed: ['m2'], kept: ['m1'], revision_before: 2, revision_after: 3 },
      provider_key: 'sk-live-should-not-render',
    },
  ],
};

async function mockDiagnostics(page: Page, contextStatus = 200) {
  await page.route('**/api/v1/conversations/*/context', (route) =>
    contextStatus === 200 ? route.fulfill({ json: CONTEXT }) : route.fulfill({ status: contextStatus, json: {} }),
  );
  await page.route('**/api/v1/conversations/*/context/operations', (route) => route.fulfill({ json: OPERATIONS }));
}

async function openApp(page: Page) {
  await mockConversations(page);
  const sessions = await mockSessions(page);
  await page.goto('/');
  await ready(page);
  return sessions;
}

test('toggling Developer mode never creates a voice session and survives reload', async ({ page }) => {
  await mockDiagnostics(page);
  const { posts } = await openApp(page);

  const toggle = page.getByRole('switch', { name: 'Developer mode' });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('[data-section="compiled"]')).toContainText('Conversation revision');
  expect(posts).toHaveLength(0);

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await expect(page.locator('[data-section="compiled"]')).toHaveCount(0);
  expect(posts).toHaveLength(0);
  expect(await page.evaluate(() => localStorage.getItem('sarjy-ui-mode'))).toBe('simple');

  await toggle.click();
  await page.reload();
  await ready(page);
  await expect(page.getByRole('switch', { name: 'Developer mode' })).toHaveAttribute('aria-checked', 'true');
  expect(posts).toHaveLength(0);
});

test('developer diagnostics are read-only and hide provider material', async ({ page }) => {
  await mockDiagnostics(page);
  const { posts } = await openApp(page);

  await page.getByRole('switch', { name: 'Developer mode' }).click();
  await expect(page.locator('[data-section="diff"]')).toContainText('removed: m2');
  await expect(page.locator('[data-section="compiled"]')).toContainText('Unavailable');
  await expect(page.locator('[data-section="latency"]')).toHaveAttribute('data-status', 'unavailable');

  const text = await page.locator('.dev-panel').innerText();
  expect(text).not.toContain('sk-live-should-not-render');
  expect(text).not.toContain('should-not-render');
  expect(text.toLowerCase()).not.toContain('api key');
  expect(posts).toHaveLength(0);
});

test('an unauthorized session gets no Developer control', async ({ page }) => {
  await mockDiagnostics(page, 403);
  const { posts } = await openApp(page);

  await expect(page.locator('.dev-panel')).toContainText('not available for this session');
  await expect(page.getByRole('switch', { name: 'Developer mode' })).toHaveCount(0);
  expect(posts).toHaveLength(0);
});
