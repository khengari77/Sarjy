import { expect, test } from '@playwright/test';
import { mockWordle, type Mark } from './support/wordleHarness';

const MARKS: Mark[] = ['absent', 'present', 'absent', 'absent', 'correct'];

type Hosted = { __sarjyWordleVoice: { tool(action: 'open' | 'refresh' | 'close'): void }; __noReload?: boolean };

/** What the model's tools do server-side: change the game, then signal the client. */
test('model tool events open the popup and update the board live without a reload', async ({ page }) => {
  const state = await mockWordle(page);
  await page.goto('/');
  await page.waitForFunction(() => Boolean((window as unknown as Hosted).__sarjyWordleVoice));
  await page.evaluate(() => ((window as unknown as Hosted).__noReload = true));
  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  await expect(dialog).toBeHidden();

  // play_wordle: "Can we play the daily Wordle?" opens the popup.
  await page.evaluate(() => (window as unknown as Hosted).__sarjyWordleVoice.tool('open'));
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('data-view', 'playing');

  // submit_wordle_guess: the server records CRANE, then the worker signals a refresh.
  state.guesses.push({ word: 'CRANE', marks: MARKS });
  await page.evaluate(() => (window as unknown as Hosted).__sarjyWordleVoice.tool('open'));
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toBeVisible();
  await expect(dialog.getByRole('gridcell', { name: 'R, in the word, wrong position' })).toBeVisible();

  // A second guess continues on the same open board.
  state.guesses.push({ word: 'SLATE', marks: ['absent', 'absent', 'absent', 'absent', 'correct'] });
  await page.evaluate(() => (window as unknown as Hosted).__sarjyWordleVoice.tool('refresh'));
  await expect(dialog.getByRole('gridcell', { name: 'S, not in the word' })).toBeVisible();
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toHaveCount(2);
  await expect(dialog).toHaveAttribute('data-view', 'playing');

  // Winning shows the terminal state with the released answer.
  state.guesses.push({ word: 'PLANT', marks: ['correct', 'correct', 'correct', 'correct', 'correct'] });
  state.status = 'won';
  state.answer = 'PLANT';
  await page.evaluate(() => (window as unknown as Hosted).__sarjyWordleVoice.tool('refresh'));
  await expect(dialog).toHaveAttribute('data-view', 'won');

  // close_wordle hides the popup.
  await page.evaluate(() => (window as unknown as Hosted).__sarjyWordleVoice.tool('close'));
  await expect(dialog).toBeHidden();
  expect(await page.evaluate(() => (window as unknown as Hosted).__noReload)).toBe(true);
  expect(state.posts).toEqual([]);
});

test('a refresh signal while the popup is closed does not open it', async ({ page }) => {
  await mockWordle(page);
  await page.goto('/');
  await page.waitForFunction(() => Boolean((window as unknown as Hosted).__sarjyWordleVoice));
  await page.evaluate(() => (window as unknown as Hosted).__sarjyWordleVoice.tool('refresh'));
  await expect(page.getByRole('dialog', { name: 'Daily Wordle' })).toBeHidden();
});
