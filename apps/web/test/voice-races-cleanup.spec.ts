import { expect, test } from '@playwright/test';
import { allowMicrophone, installRaceRuntime, mockConversations, mockSessions, ready } from './support/raceHarness';

test('cancelling during microphone startup clears the live session and skips meters', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page, { autoConnect: true, deferStartMic: true });

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toBeVisible();
  expect(posts).toHaveLength(1);
  await expect.poll(() => page.evaluate(() => window.__race?.hasPendingStartMic())).toBe(true);

  await page.evaluate(() => {
    if (window.__race) window.__race.audioLevel = 0.8;
  });
  await page.getByRole('button', { name: 'Cancel connecting', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);

  await page.evaluate(() => window.__race?.resolveStartMic());
  await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 300)));

  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'idle');
  await expect(page.locator('.orb-button')).toHaveAttribute('data-output-level', '0.00');
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
});

test('a stalled failure cleanup cannot overwrite a replacement call', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page, { autoConnect: true, startMicFail: true, deferDisconnect: true });

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.locator('[data-code="MICROPHONE_PERMISSION_DENIED"]')).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__race?.hasPendingDisconnect())).toBe(true);

  await page.getByRole('button', { name: 'Cancel connecting', exact: true }).click();
  await page.evaluate(() => window.__race?.setStartMicFail(false));
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect.poll(() => posts.length).toBe(2);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');

  // Resolve the first attempt's stalled disconnect after the replacement is live.
  await page.evaluate(() => window.__race?.resolveDisconnect());
  await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 300)));

  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  expect(ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  expect(ends.some((url) => url.endsWith('/sess-2/end'))).toBe(false);
});
