import { expect, test } from '@playwright/test';
import { FACTS, mockApi, openMemoryTab, waitForShell } from './memory-harness';

// Freshness and result-honesty regressions for the memory explorer. Kept apart
// from the main flow spec so each file stays within the lint budget.

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});
test('a fact saved while the panel was hidden appears when the tab is reopened', async ({ page }) => {
  // P1-2: the panel stays mounted inside the hidden tab, so a list loaded
  // before a voice save must not keep claiming "No saved facts yet".
  const calls = await mockApi(page, []);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  const panel = await openMemoryTab(page);
  await expect(panel).toContainText('No saved facts yet.');

  // Hide the tab, then let a save land server-side (a voice turn finalising).
  await page.locator('#shell-right').getByRole('tab', { name: 'Controls' }).click();
  calls.setFacts([FACTS[0]]);

  // Reopening the tab refreshes the stale list without a conversation switch.
  await page.locator('#shell-right').getByRole('tab', { name: 'Memories' }).click();
  await expect(panel).toContainText('Prefers tea over coffee');
  await expect(panel).not.toContainText('No saved facts yet.');
  const reopened = calls.memories.slice(-1)[0];
  expect(new URL(reopened).searchParams.get('conversation_id')).toBe('c1');
});
