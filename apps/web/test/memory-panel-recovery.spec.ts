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

test('a failed refresh after a confirmed exclusion never claims nothing changed', async ({ page }) => {
  // F5: the mutation succeeded, so a list failure must be read-shaped. The old
  // copy ("Nothing was changed") contradicted "Exclusion confirmed" beside it.
  const calls = await mockApi(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  const panel = await openMemoryTab(page);
  await expect(panel).toContainText('Prefers tea over coffee');

  const includedRow = panel.locator('[data-included="true"]');
  await includedRow.getByRole('button', { name: 'Exclude…' }).click();
  calls.failList(1);
  await panel.getByTestId('exclusion-review').getByRole('button', { name: 'Confirm exclusion' }).click();

  await expect(panel.getByTestId('exclusion-confirmed')).toContainText('Exclusion confirmed');
  const error = panel.getByRole('alert');
  await expect(error).toContainText('Saved facts could not be reloaded');
  await expect(error).toContainText('Any confirmed change still stands');
  await expect(panel).not.toContainText('Nothing was changed');
});

test('undo that restores nothing says so instead of claiming a fact is eligible', async ({ page }) => {
  // F1: when the exclusion lineage still blocks the fact, the response is
  // empty and the panel must not say "1 fact is eligible again".
  const calls = await mockApi(page);
  calls.setUndoRestored([]);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  const panel = await openMemoryTab(page);
  await expect(panel).toContainText('Prefers tea over coffee');

  await panel.locator('[data-included="true"]').getByRole('button', { name: 'Exclude…' }).click();
  await panel.getByTestId('exclusion-review').getByRole('button', { name: 'Confirm exclusion' }).click();
  await expect(panel.getByTestId('exclusion-confirmed')).toBeVisible();

  await panel.getByRole('button', { name: 'Undo exclusion' }).click();
  const undone = panel.getByTestId('exclusion-undone');
  await expect(undone).toContainText('Undo made no fact eligible');
  await expect(undone).toContainText('No fact became eligible');
  await expect(undone).not.toContainText('1 fact');
  await expect(panel).not.toContainText('is eligible for this conversation');
});
