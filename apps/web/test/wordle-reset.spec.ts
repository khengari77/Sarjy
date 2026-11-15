import { expect, test } from '@playwright/test';
import { mockWordle, openWordle, type Mark } from './support/wordleHarness';

const MARKS: Mark[] = ['absent', 'present', 'absent', 'absent', 'correct'];

test('the reset button asks first, then clears the board and status without a reload', async ({ page }) => {
  const state = await mockWordle(page, { guesses: [{ word: 'CRANE', marks: MARKS }] });
  const dialog = await openWordle(page);
  await page.evaluate(() => ((window as unknown as { __noReload?: boolean }).__noReload = true));
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toBeVisible();
  await expect(dialog.locator('.wordle-status')).toContainText('5');

  await dialog.getByRole('button', { name: "Reset today's game" }).click();
  const confirm = dialog.getByRole('alertdialog', { name: "Reset today's game?" });
  await expect(confirm).toBeVisible();
  await expect(confirm.getByRole('button', { name: 'Keep playing' })).toBeFocused();
  expect(state.resets).toBe(0);

  // Escape or "Keep playing" keeps everything and leaves the game open.
  await page.keyboard.press('Escape');
  await expect(confirm).toBeHidden();
  await expect(dialog).toBeVisible();
  expect(state.resets).toBe(0);

  await dialog.getByRole('button', { name: "Reset today's game" }).click();
  await confirm.getByRole('button', { name: 'Reset game' }).click();
  await expect(confirm).toBeHidden();
  expect(state.resets).toBe(1);
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toHaveCount(0);
  await expect(dialog.locator('.wordle-status')).toContainText('6');
  await expect(dialog).toHaveAttribute('data-view', 'playing');
  expect(await page.evaluate(() => (window as unknown as { __noReload?: boolean }).__noReload)).toBe(true);
});

test('a reset after a finished game replays the same puzzle', async ({ page }) => {
  const state = await mockWordle(page, {
    status: 'won',
    answer: 'CRANE',
    guesses: [{ word: 'CRANE', marks: ['correct', 'correct', 'correct', 'correct', 'correct'] }],
  });
  const dialog = await openWordle(page);
  await expect(dialog).toHaveAttribute('data-view', 'won');
  await dialog.getByRole('button', { name: "Reset today's game" }).click();
  await dialog.getByRole('alertdialog').getByRole('button', { name: 'Reset game' }).click();
  await expect(dialog).toHaveAttribute('data-view', 'playing');
  expect(state.resets).toBe(1);
});
