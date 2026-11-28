import { expect, test, type Page } from '@playwright/test';
import { gotoHistory } from './support/historyHarness';

const CLOCK_START = new Date('2024-01-01T00:00:00Z');
const RETRYING = 'Having trouble loading conversations. Retrying...';
const FINAL_ERROR = 'Could not load conversations.';

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

/** Stop the clock after the page has loaded so retry timers only fire on demand. */
async function freezeAfterLoad(page: Page) {
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(now + 1_000);
}

test('a transient list failure retries automatically and recovers inside the sidebar', async ({ page }) => {
  let listCalls = 0;
  let releaseFirst: () => void = () => {};
  const firstRequest = new Promise<void>((resolve) => (releaseFirst = resolve));
  await page.route('**/api/v1/conversations', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    listCalls += 1;
    if (listCalls === 1) {
      await firstRequest;
      await route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
      return;
    }
    await route.fulfill({ json: [{ id: 'c1', title: 'Trip to Paris', revision: 0 }] });
  });
  await page.route('**/api/v1/demo-session', async (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.clock.install({ time: CLOCK_START });
  await gotoHistory(page);
  await freezeAfterLoad(page);
  releaseFirst();

  const error = page.locator('[data-code="API_UNAVAILABLE"]');
  await expect(error).toContainText(RETRYING, { timeout: 5_000 });
  expect(listCalls).toBe(1);

  await page.clock.fastForward(1_500);
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible({ timeout: 5_000 });
  expect(listCalls).toBe(2);
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'idle');
});

test('an exhausted automatic retry shows the final error and manual Retry recovers', async ({ page }) => {
  let listCalls = 0;
  let failing = true;
  let releaseFirst: () => void = () => {};
  const firstRequest = new Promise<void>((resolve) => (releaseFirst = resolve));
  await page.route('**/api/v1/conversations', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    listCalls += 1;
    if (listCalls === 1) await firstRequest;
    if (failing) {
      await route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
      return;
    }
    await route.fulfill({ json: [{ id: 'c1', title: 'Trip to Paris', revision: 0 }] });
  });
  await page.route('**/api/v1/demo-session', async (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.clock.install({ time: CLOCK_START });
  await gotoHistory(page);
  await freezeAfterLoad(page);
  releaseFirst();

  const error = page.locator('[data-code="API_UNAVAILABLE"]');
  await expect(error).toContainText(RETRYING, { timeout: 5_000 });
  expect(listCalls).toBe(1);

  await page.clock.fastForward(1_500);
  await expect.poll(() => listCalls, { timeout: 5_000 }).toBe(2);
  // Jump again if the failed response had not scheduled the 4 s timer yet.
  for (let attempt = 0; attempt < 3 && listCalls < 3; attempt += 1) await page.clock.fastForward(4_000);
  await expect.poll(() => listCalls, { timeout: 5_000 }).toBe(3);

  await expect(error).toContainText(FINAL_ERROR, { timeout: 5_000 });
  const retry = page.getByRole('button', { name: 'Retry', exact: true });
  await expect(retry).toBeVisible();

  failing = false;
  await retry.click();
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible({ timeout: 5_000 });
  expect(listCalls).toBe(4);
});
