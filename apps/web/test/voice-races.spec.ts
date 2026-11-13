import { expect, test } from '@playwright/test';
import {
  allowMicrophone,
  deferMicrophone,
  installRaceRuntime,
  mockConversations,
  mockSessions,
  ready,
} from './support/raceHarness';

test('switching conversations while connecting cancels the attempt instead of binding text to it', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page);

  await page.goto('/');
  await ready(page);
  await expect(page.getByRole('button', { name: 'Chat A', exact: true })).toHaveAttribute('aria-current', 'page');

  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].body.conversation_id).toBe('c1');
  await expect.poll(() => page.evaluate(() => window.__race?.hasPending('sess-1'))).toBe(true);

  await page.getByRole('button', { name: 'Chat B', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Chat B', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toHaveCount(0);

  await page.evaluate(() => window.__race?.resolveConnect('sess-1'));
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.__race?.stats())).toMatchObject({ connects: ['sess-1'], sent: [] });
});

test('a cancelled attempt cannot disconnect its replacement', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page);

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__race?.hasPending('sess-1'))).toBe(true);
  await page.getByRole('button', { name: 'Cancel connecting', exact: true }).click();

  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect.poll(() => posts.length).toBe(2);
  await expect.poll(() => page.evaluate(() => window.__race?.hasPending('sess-2'))).toBe(true);
  await page.evaluate(() => window.__race?.resolveConnect('sess-2'));
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');

  await page.evaluate(() => window.__race?.resolveConnect('sess-1'));
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  expect(ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  expect(ends.some((url) => url.endsWith('/sess-2/end'))).toBe(false);
  expect(await page.evaluate(() => window.__race?.stats())).toMatchObject({
    connects: ['sess-1', 'sess-2'],
    disconnects: 0,
    cancels: 1,
  });
});

test('cancelling while microphone permission is pending creates no session', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await deferMicrophone(page);
  await installRaceRuntime(page);

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toBeVisible();
  expect(posts).toHaveLength(0);

  await page.getByRole('button', { name: 'Cancel connecting', exact: true }).click();
  await page.evaluate(() => window.__permission?.resolve());
  await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 250)));

  expect(posts).toHaveLength(0);
  expect(ends).toHaveLength(0);
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  expect((await page.evaluate(() => window.__race?.stats()))?.cancels).toBe(1);
});

test('microphone startup failure clears the ended session and allows retry', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page, { autoConnect: true, startMicFail: true });

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();

  await expect(page.locator('[data-code="MICROPHONE_PERMISSION_DENIED"]')).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  await expect(page.locator('.orb-button')).toHaveAttribute('data-output-level', '0.00');

  const retry = page.getByRole('button', { name: 'Retry microphone', exact: true });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect.poll(() => posts.length).toBe(2);
});
