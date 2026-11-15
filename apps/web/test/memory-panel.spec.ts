import { expect, test, type Page } from '@playwright/test';
import { FACTS, mockApi, openMemoryTab, waitForShell } from './memory-harness';

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test.use({ colorScheme: 'light' });

async function open(page: Page, width = 1280, height = 720) {
  await page.setViewportSize({ width, height });
  await page.goto('/');
  await waitForShell(page);
  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  return openMemoryTab(page);
}

const row = (panel: ReturnType<Page['locator']>, text: string) => panel.locator('li.memory-row', { hasText: text });

test('lists every non-deleted memory, hidden ones marked, each with named icon buttons', async ({ page }) => {
  const calls = await mockApi(page);
  const panel = await open(page);

  await expect(row(panel, 'Prefers tea over coffee')).toBeVisible();
  const paris = row(panel, 'Hotel in Paris');
  await expect(paris).toHaveAttribute('data-hidden', 'true');
  await expect(paris).toContainText('Hidden');
  await expect(panel).not.toContainText('Exclude…');
  // The list never filters hidden rows out of view and never asks for deleted ones.
  expect(calls.memories.every((url) => !new URL(url).searchParams.has('status'))).toBe(true);

  const tea = row(panel, 'Prefers tea over coffee');
  for (const name of ['Hide memory', 'Delete memory']) {
    const button = tea.getByRole('button', { name, exact: true });
    await expect(button).toHaveAttribute('title', name);
    const box = (await button.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
    await expect(button).toHaveText('');
  }
  await expect(paris.getByRole('button', { name: 'Restore memory' })).toHaveAttribute('title', 'Restore memory');
  // Delete stays available on a hidden row.
  await expect(paris.getByRole('button', { name: 'Delete memory' })).toBeEnabled();
});

test('hide toggles the row in place without a prompt and restore flips it back', async ({ page }) => {
  const calls = await mockApi(page);
  const panel = await open(page);
  const tea = row(panel, 'Prefers tea over coffee');
  const positionBefore = await panel.locator('li.memory-row').evaluateAll((rows) => rows.map((r) => r.getAttribute('data-memory-id')));

  await tea.getByRole('button', { name: 'Hide memory' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(tea).toHaveAttribute('data-hidden', 'true');
  await expect(tea).toContainText('Hidden');
  // Same button, flipped.
  const restore = tea.getByRole('button', { name: 'Restore memory' });
  await expect(restore).toHaveAttribute('title', 'Restore memory');
  await expect(tea.getByRole('button', { name: 'Hide memory' })).toHaveCount(0);
  expect(calls.visibility).toEqual([{ id: 'mem-tea', method: 'POST', action: 'hide' }]);
  // The row did not move.
  expect(await panel.locator('li.memory-row').evaluateAll((rows) => rows.map((r) => r.getAttribute('data-memory-id')))).toEqual(
    positionBefore,
  );

  await restore.click();
  await expect(tea).toHaveAttribute('data-hidden', 'false');
  await expect(tea.getByRole('button', { name: 'Hide memory' })).toBeVisible();
  expect(calls.visibility.at(-1)).toEqual({ id: 'mem-tea', method: 'POST', action: 'unhide' });
});

test('delete asks first: Escape cancels and returns focus, Delete removes the row', async ({ page }) => {
  const calls = await mockApi(page);
  const panel = await open(page);
  const trigger = row(panel, 'Lives in Riyadh').getByRole('button', { name: 'Delete memory' });

  await trigger.click();
  const dialog = page.getByRole('dialog', { name: 'Delete this memory?' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('cannot be undone');
  await expect(dialog).toContainText('never by voice');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();

  // Focus is trapped: Tab cycles within the dialog.
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Delete', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(calls.visibility).toHaveLength(0);
  await expect(row(panel, 'Lives in Riyadh')).toBeVisible();
  // The drawer itself stayed open.
  await expect(panel).toBeVisible();

  await trigger.click();
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(row(panel, 'Lives in Riyadh')).toHaveCount(0);
  await expect(dialog).toHaveCount(0);
  expect(calls.visibility).toEqual([{ id: 'mem-riyadh', method: 'DELETE', action: 'delete' }]);
});

test('a failed call leaves the row unchanged with a short inline message', async ({ page }) => {
  const calls = await mockApi(page);
  const panel = await open(page);
  calls.failVisibility(1);

  const tea = row(panel, 'Prefers tea over coffee');
  await tea.getByRole('button', { name: 'Hide memory' }).click();
  await expect(tea.getByTestId('memory-row-error')).toContainText("Couldn't hide this memory");
  await expect(tea).toHaveAttribute('data-hidden', 'false');
  await expect(tea.getByRole('button', { name: 'Hide memory' })).toBeEnabled();

  // A retry succeeds and clears the message.
  await tea.getByRole('button', { name: 'Hide memory' }).click();
  await expect(tea).toHaveAttribute('data-hidden', 'true');
});

test('buttons disable while in flight and a double-click sends one request', async ({ page }) => {
  const calls = await mockApi(page);
  const panel = await open(page);
  const hold = calls.holdVisibility();

  const tea = row(panel, 'Prefers tea over coffee');
  const hide = tea.getByRole('button', { name: 'Hide memory' });
  await hide.dblclick();
  await expect(hide).toBeDisabled();
  await expect(tea.getByRole('button', { name: 'Delete memory' })).toBeDisabled();
  expect(calls.visibility).toHaveLength(1);
  hold.release();
  await expect(tea).toHaveAttribute('data-hidden', 'true');
  expect(calls.visibility).toHaveLength(1);
});

test('a memory hidden or restored by voice updates in place without a reload', async ({ page }) => {
  const calls = await mockApi(page);
  const panel = await open(page);
  await expect(row(panel, 'Prefers tea over coffee')).toBeVisible();

  // "Forget my tea" lands server-side, then the turn finalises (turn count changes).
  calls.setFacts(FACTS.map((fact) => (fact.id === 'mem-tea' ? { ...fact, status: 'hidden' } : fact)));
  calls.setTurns([
    { id: 't1', role: 'user', text: 'forget my tea', status: 'complete' },
    { id: 't2', role: 'assistant', text: 'Done.', status: 'complete' },
  ]);
  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();

  await expect(row(panel, 'Prefers tea over coffee')).toHaveAttribute('data-hidden', 'true');

  // "Undo" restores it the same way.
  calls.setFacts(FACTS);
  calls.setTurns([
    { id: 't1', role: 'user', text: 'forget my tea', status: 'complete' },
    { id: 't2', role: 'assistant', text: 'Done.', status: 'complete' },
    { id: 't3', role: 'user', text: 'undo', status: 'complete' },
  ]);
  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  await expect(row(panel, 'Prefers tea over coffee')).toHaveAttribute('data-hidden', 'false');
});
