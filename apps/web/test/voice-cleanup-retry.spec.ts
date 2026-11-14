import { expect, test } from '@playwright/test';
import { allowMicrophone, installRaceRuntime, mockConversations, mockSessions, ready } from './support/raceHarness';

test('a failed startup cleanup stays actionable instead of turning into Start', async ({ page }) => {
  await mockConversations(page);
  const { ends } = await mockSessions(page, { failEnds: 1 });
  await allowMicrophone(page);
  await installRaceRuntime(page, { autoConnect: true, startMicFail: true });

  await page.goto('/');
  await ready(page);

  // Session sess-1 is admitted, then microphone startup fails; its release also
  // fails, so the admission remains the user's to clean up.
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  const retryCleanup = page.getByRole('button', { name: 'Retry ending previous call', exact: true });
  await expect(retryCleanup).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  await expect.poll(() => ends.filter((url) => url.endsWith('/sess-1/end')).length).toBe(1);

  // Starting a new call must not drop the outstanding cleanup.
  await page.evaluate(() => window.__race?.setStartMicFail(false));
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'End call', exact: true }).click();
  await expect(retryCleanup).toBeVisible();

  await retryCleanup.click();
  await expect(retryCleanup).toHaveCount(0);
  await expect.poll(() => ends.filter((url) => url.endsWith('/sess-1/end')).length).toBe(2);
});
