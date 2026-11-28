import { expect, test } from '@playwright/test';
import { gotoHistory } from './support/historyHarness';

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('a transient list failure retries automatically and recovers inside the sidebar', async ({ page }) => {
  let listCalls = 0;
  await page.route('**/api/v1/conversations', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    listCalls += 1;
    if (listCalls === 1) {
      await route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
      return;
    }
    await route.fulfill({ json: [{ id: 'c1', title: 'Trip to Paris', revision: 0 }] });
  });
  await page.route('**/api/v1/demo-session', async (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.clock.install();
  await gotoHistory(page);

  const error = page.locator('[data-code="API_UNAVAILABLE"]');
  await expect(error).toContainText('Having trouble loading conversations. Retrying...');
  expect(listCalls).toBe(1);

  await page.clock.fastForward(1_500);
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible({ timeout: 5_000 });
  expect(listCalls).toBe(2);
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'idle');
});
