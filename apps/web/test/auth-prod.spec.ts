import { expect, test } from '@playwright/test';
import { rm } from 'node:fs/promises';
import { resolve } from 'node:path';

const BASE = 'http://localhost:4181';
const TOKEN = 'test-review-token-0123456789abcdef';
const STATE = resolve('test-results/auth-prod-state.json');

test.beforeAll(async () => {
  await rm(STATE, { force: true });
});

test('the door sends same-origin so native form posts clear production CSRF', async ({ request }) => {
  const response = await request.get(`${BASE}/`);
  expect(response.status()).toBe(200);
  expect(response.headers()['referrer-policy']).toBe('same-origin');
  expect(response.headers()['cache-control']).toBe('no-store');
  expect(await response.text()).toContain('id="access-token"');
});

test('manual token entry works with JavaScript disabled in a production build', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await expect(page.locator('#access-token')).toBeVisible();
  await page.getByLabel('Access token').fill(TOKEN);
  await page.getByRole('button', { name: 'Enter' }).click();
  await expect(page).toHaveURL(`${BASE}/`);
  await expect(page.locator('#voice-stage')).toBeVisible();

  // Sign-out is also a native form POST; it must not 403 and retain the cookie.
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.locator('#access-token')).toBeVisible();
  expect((await context.cookies()).some((cookie) => cookie.name === 'sarjy_review_session')).toBe(false);
  await context.close();
});

test('a hash token lands in the app in a production build', async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/#token=${TOKEN}`);
  await expect(page).toHaveURL(`${BASE}/`);
  await expect(page.locator('#voice-stage')).toBeVisible();
  await context.close();
});

test('a token on a non-root path is stripped from next in production too', async ({ request }) => {
  const response = await request.get(`${BASE}/some/page?token=${TOKEN}&keep=1`, {
    maxRedirects: 0,
    headers: { accept: 'text/html' },
  });
  expect(response.status()).toBe(303);
  const location = response.headers()['location'] ?? '';
  expect(location).not.toContain(TOKEN);
  expect(location).toBe(`/?next=${encodeURIComponent('/some/page?keep=1')}`);
});
