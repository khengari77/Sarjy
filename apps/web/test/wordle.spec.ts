import { mkdirSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { mockWordle, openWordle, type Mark } from './support/wordleHarness';

test('loads the server board, resumes guesses, and never shows the answer early', async ({ page }) => {
  const state = await mockWordle(page, {
    status: 'in_progress',
    answer: 'PLANT',
    guesses: [
      { word: 'CRANE', marks: ['absent', 'present', 'absent', 'absent', 'correct'] },
      { word: 'ALLEY', marks: ['correct', 'present', 'absent', 'present', 'absent'] },
    ],
  });
  const dialog = await openWordle(page);
  await expect(dialog).toHaveAttribute('data-view', 'playing');
  await expect(dialog.getByText('Friday 13 November')).toBeVisible();
  await expect(dialog.getByRole('grid')).toHaveAttribute('aria-rowcount', '6');
  await expect(dialog.getByRole('gridcell', { name: 'R, in the word, wrong position' })).toBeVisible();
  await expect(dialog.getByRole('gridcell', { name: 'A, correct position' })).toBeVisible();
  await expect(dialog.locator('.wordle-status')).toHaveText('4 guesses left.');
  await expect(dialog.getByText('Played')).toBeVisible();
  await expect(dialog.getByText('3', { exact: true })).toBeVisible();
  await expect(page.getByText('PLANT')).toHaveCount(0);
  expect(state.todayCalls).toBe(1);
});

test('resumes a terminal game with the server answer and locked input', async ({ page }) => {
  await mockWordle(page, {
    status: 'lost',
    answer: 'PLANT',
    guesses: Array.from({ length: 6 }, (_, index) => ({
      word: ['CRANE', 'ALLEY', 'SLATE', 'BRICK', 'MOUND', 'FUZZY'][index],
      marks: ['absent', 'absent', 'absent', 'absent', 'absent'] as Mark[],
    })),
  });
  const dialog = await openWordle(page);
  await expect(dialog).toHaveAttribute('data-view', 'lost');
  await expect(dialog.locator('.wordle-result p').first()).toHaveText('Out of guesses. The answer was PLANT.');
  await expect(dialog.locator('.wordle-answer')).toHaveText('The answer was PLANT.');
  await expect(dialog.getByRole('button', { name: 'Enter guess' })).toBeDisabled();
});

test('rejects an invalid word without losing input, then reveals server marks for duplicates', async ({ page }) => {
  let attempt = 0;
  const state = await mockWordle(page, {
    onGuess: () => {
      attempt += 1;
      if (attempt === 1) return { code: 'WORDLE_GUESS_INVALID', marks: [] };
      return { marks: ['absent', 'present', 'absent', 'correct', 'absent'] };
    },
  });
  const dialog = await openWordle(page);
  await page.keyboard.type('ALLEY');
  await page.keyboard.press('Enter');
  await expect(dialog.locator('.wordle-status')).toHaveText('Enter a five-letter word using A–Z letters.');
  await expect(dialog.getByRole('gridcell', { name: 'A, row 1, column 1, not submitted' })).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(dialog.getByRole('gridcell', { name: 'L, in the word, wrong position' })).toBeVisible();
  await expect(dialog.getByRole('gridcell', { name: 'L, row 1, column 3, not submitted' })).toHaveCount(0);
  await expect(dialog.getByRole('gridcell', { name: 'L, not in the word' })).toBeVisible();
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toBeVisible();
  expect(state.posts).toHaveLength(2);
  expect(state.posts[0].request_id).toBe(state.posts[1].request_id);
  expect(state.posts[0].guess).toBe('ALLEY');
  await expect(dialog.locator('.wordle-status')).toHaveText('5 guesses left.');
});

test('uses a new request id when the word changes after a failure', async ({ page }) => {
  let attempt = 0;
  const state = await mockWordle(page, {
    onGuess: () => {
      attempt += 1;
      return attempt === 1
        ? { code: 'WORDLE_RATE_LIMITED', marks: [] }
        : { marks: ['absent', 'absent', 'absent', 'absent', 'absent'] };
    },
  });
  const dialog = await openWordle(page);
  await page.keyboard.type('ALLEY');
  await page.keyboard.press('Enter');
  await expect(dialog.locator('.wordle-status')).toHaveText('Too many guesses just now. Try again in a moment.');
  await page.keyboard.press('Backspace');
  await page.keyboard.type('X');
  await page.keyboard.press('Enter');
  await expect(dialog.getByRole('gridcell', { name: 'X, not in the word' })).toBeVisible();
  expect(state.posts).toHaveLength(2);
  expect(state.posts[0].request_id).not.toBe(state.posts[1].request_id);
  expect(state.posts[1].guess).toBe('ALLEX');
});

test('plays with the on-screen keyboard and disables it after a win', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname);
  });
  const state = await mockWordle(page, {
    onGuess: () => ({ marks: ['correct', 'correct', 'correct', 'correct', 'correct'], status: 'won', answer: 'CRANE' }),
  });
  const dialog = await openWordle(page);
  for (const letter of ['C', 'R', 'A', 'N', 'E'])
    await dialog.getByRole('button', { name: letter, exact: true }).click();
  await dialog.getByRole('button', { name: 'Enter guess' }).click();
  await expect(dialog.getByText('You won in 1 guess!', { exact: true })).toBeVisible();
  await expect(dialog.locator('.wordle-answer')).toHaveText('The answer was CRANE.');
  await expect(dialog.getByRole('button', { name: 'Enter guess' })).toBeDisabled();
  await page.keyboard.type('PLANT');
  await page.keyboard.press('Enter');
  expect(state.posts).toHaveLength(1);
  expect(writes.filter((path) => path.includes('/conversations') || path.includes('/memor'))).toEqual([]);
});

test('shows an honest unavailable state with retry and no fabricated board', async ({ page }) => {
  const state = await mockWordle(page, { available: false });
  const dialog = await openWordle(page);
  await expect(dialog).toHaveAttribute('data-view', 'unavailable');
  await expect(dialog.getByRole('alert')).toContainText("Today's Wordle isn't available right now.");
  await expect(dialog.getByRole('grid')).toHaveCount(0);
  state.available = true;
  state.status = 'in_progress';
  await dialog.getByRole('button', { name: 'Retry' }).click();
  await expect(dialog).toHaveAttribute('data-view', 'playing');
  await expect(dialog.getByRole('grid')).toBeVisible();
});

test('distinguishes a disabled build from a missing puzzle', async ({ page }) => {
  await mockWordle(page, { available: false, code: 'WORDLE_DISABLED' });
  const dialog = await openWordle(page);
  await expect(dialog).toHaveAttribute('data-view', 'unavailable');
  await expect(dialog.getByRole('alert')).toContainText('Wordle is turned off in this build.');
  await expect(dialog.getByRole('grid')).toHaveCount(0);
});

test('reports unavailable stats honestly instead of inventing them', async ({ page }) => {
  await mockWordle(page, { statsCode: 503 });
  const dialog = await openWordle(page);
  await expect(dialog.getByRole('definition').filter({ hasText: 'unavailable' })).toBeVisible();
  await expect(dialog.getByText('Played')).toHaveCount(0);
});

test('opens and closes without touching the voice session and restores focus', async ({ page }) => {
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionPosts += 1;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });
  await mockWordle(page);
  await page.goto('/');
  const entry = page.locator('.wordle-entry:visible');
  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  await expect(async () => {
    await entry.click();
    await expect(dialog).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 20_000 });
  await expect(dialog).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(entry).toBeFocused();
  expect(sessionPosts).toBe(0);
});

test('respects reduced motion for the reveal', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockWordle(page, {
    onGuess: () => ({ marks: ['correct', 'correct', 'correct', 'correct', 'correct'], status: 'won', answer: 'CRANE' }),
  });
  const dialog = await openWordle(page);
  await page.keyboard.type('CRANE');
  await page.keyboard.press('Enter');
  const tile = dialog.locator('.wordle-tile-fresh').first();
  await expect(tile).toBeVisible();
  expect(await tile.evaluate((node) => getComputedStyle(node).animationName)).toBe('none');
});

test('fits a 360px viewport without horizontal scroll and keeps 44px keys', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await mockWordle(page);
  const dialog = await openWordle(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const stageOverflow = await dialog.evaluate((node) => node.scrollWidth - node.clientWidth);
  expect(stageOverflow).toBeLessThanOrEqual(1);
  await expect(dialog.getByRole('grid')).toBeVisible();
  const heights = await dialog
    .getByRole('button', { name: /^[A-Z]$/ })
    .evaluateAll((keys) => keys.map((key) => key.getBoundingClientRect().height));
  expect(Math.min(...heights)).toBeGreaterThanOrEqual(44);
});

const THEMES = ['dark', 'light'] as const;
const WIDTHS = [390, 1440] as const;

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test(`screenshot ${theme} theme at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await mockWordle(page, {
        guesses: [
          { word: 'CRANE', marks: ['absent', 'present', 'absent', 'absent', 'correct'] },
          { word: 'ALLEY', marks: ['correct', 'present', 'absent', 'present', 'absent'] },
        ],
      });
      const dialog = await openWordle(page, theme);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      mkdirSync('test-results/wordle', { recursive: true });
      await page.screenshot({ path: `test-results/wordle/wordle-${theme}-${width}.png` });
      await expect(dialog.getByRole('grid')).toBeVisible();
    });
  }
}
