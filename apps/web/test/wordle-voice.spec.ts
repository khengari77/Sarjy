import { expect, test } from '@playwright/test';
import { mockWordle, openWordle, wordleVoice, type Mark } from './support/wordleHarness';

const ALL_ABSENT: Mark[] = ['absent', 'absent', 'absent', 'absent', 'absent'];

test('only finalized transcripts act: partials never open or submit', async ({ page }) => {
  const state = await mockWordle(page);
  await openWordle(page);
  await page.getByRole('button', { name: 'Close Wordle' }).click();
  const voice = await wordleVoice(page);

  const partial = await voice.finalize({ text: 'guess crane', segmentId: 'p1', epoch: 1, final: false });
  expect(partial).toMatchObject({ handled: false, ignored: 'partial' });
  expect(state.posts).toEqual([]);
  expect((await voice.state()).open).toBe(false);

  const assistant = await voice.finalize({
    text: 'guess crane',
    segmentId: 'a1',
    epoch: 1,
    speaker: 'assistant',
  });
  expect(assistant).toMatchObject({ handled: false, ignored: 'assistant' });
  expect(state.posts).toEqual([]);

  const play = await voice.finalize({ text: 'Play Wordle', segmentId: 'f1', epoch: 1 });
  expect(play.command).toBe('play');
  await expect(page.getByRole('dialog', { name: 'Daily Wordle' })).toHaveAttribute('data-view', 'playing');

  const replay = await voice.finalize({ text: 'Play Wordle', segmentId: 'f1', epoch: 1 });
  expect(replay).toMatchObject({ handled: false, ignored: 'duplicate' });
  expect(state.todayCalls).toBe(1); // the board stays loaded; voice never refetches a resumed game
});

test('normalizes a spelled guess, submits once, and speaks the server marks', async ({ page }) => {
  const state = await mockWordle(page, {
    onGuess: () => ({ marks: ['absent', 'present', 'absent', 'absent', 'correct'] }),
  });
  const voice = await wordleVoice(page);
  await voice.finalize({ text: 'play wordle', segmentId: 's1', epoch: 1 });

  const result = await voice.finalize({ text: 'Guess C R A N E', segmentId: 's2', epoch: 1 });
  expect(result.command).toBe('guess');
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0].guess).toBe('CRANE');
  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toBeVisible();
  const spoken = await voice.spoken();
  expect(spoken).toContain('CRANE');
  expect(spoken).toContain('E correct position');
  expect(spoken).toContain('5 guesses left.');
  expect(spoken).not.toContain('PLANT');

  const replay = await voice.finalize({ text: 'guess crane', segmentId: 's2', epoch: 1 });
  expect(replay).toMatchObject({ handled: false, ignored: 'duplicate' });
  expect(state.posts).toHaveLength(1);
});

test('rejects a malformed guess with a spoken re-prompt and no submission', async ({ page }) => {
  const state = await mockWordle(page);
  const voice = await wordleVoice(page);
  await voice.finalize({ text: 'play wordle', segmentId: 's1', epoch: 1 });

  const result = await voice.finalize({ text: 'guess CRANE EXTRA', segmentId: 's2', epoch: 1 });
  expect(result.command).toBe('guess_invalid');
  expect(state.posts).toEqual([]);
  expect(await voice.spoken()).toContain('five-letter word');
});

test('asks for clarification on ambiguous speech without mutating anything', async ({ page }) => {
  const state = await mockWordle(page);
  const voice = await wordleVoice(page);

  const result = await voice.finalize({ text: 'play wordle with my friends', segmentId: 's1', epoch: 1 });
  expect(result.command).toBe('ambiguous');
  expect((await voice.state()).open).toBe(false);
  expect(state.todayCalls).toBe(0);
  expect(await voice.spoken()).toContain('What would you like');
});

test('idempotent retries reuse the guess request id after a network failure', async ({ page }) => {
  await mockWordle(page);
  let fail = true;
  const ids: string[] = [];
  await page.route('**/api/v1/wordle/guesses', async (route) => {
    ids.push((route.request().postDataJSON() as { request_id: string }).request_id);
    if (fail) await route.abort('failed');
    else await route.fallback();
  });
  const voice = await wordleVoice(page);
  await voice.finalize({ text: 'play wordle', segmentId: 's1', epoch: 1 });

  await voice.finalize({ text: 'guess crane', segmentId: 's2', epoch: 1 });
  expect(await voice.spoken()).toContain("didn't go through");
  expect((await voice.state()).guesses).toBe(0);

  fail = false;
  const retry = await voice.finalize({ text: 'guess crane', segmentId: 's3', epoch: 1 });
  expect(retry.command).toBe('guess');
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
  expect((await voice.state()).guesses).toBe(1);
});

test('reads remaining attempts and prior guesses from the server board', async ({ page }) => {
  await mockWordle(page, {
    guesses: [
      { word: 'CRANE', marks: ALL_ABSENT },
      { word: 'SLATE', marks: ALL_ABSENT },
    ],
    status: 'in_progress',
  });
  const voice = await wordleVoice(page);

  await voice.finalize({ text: 'how many guesses left', segmentId: 's1', epoch: 1 });
  expect(await voice.spoken()).toBe('4 guesses left.');
  await voice.finalize({ text: 'what did I guess', segmentId: 's2', epoch: 1 });
  expect(await voice.spoken()).toContain('CRANE, SLATE');
  expect((await voice.state()).open).toBe(false);
});

test('never speaks or renders the answer before terminal, and refuses a live spoiler', async ({ page }) => {
  await mockWordle(page, { status: 'in_progress', answer: 'PLANT', leakAnswer: true });
  const voice = await wordleVoice(page);
  await voice.finalize({ text: 'play wordle', segmentId: 's1', epoch: 1 });

  const first = await voice.finalize({ text: 'reveal the answer', segmentId: 's2', epoch: 1 });
  expect(first.command).toBe('reveal');
  expect(await voice.spoken()).toContain('Say yes');
  await expect(page.getByText('PLANT')).toHaveCount(0);

  const confirmed = await voice.finalize({ text: 'yes', segmentId: 's3', epoch: 1 });
  expect(confirmed.command).toBe('confirm');
  expect(await voice.spoken()).toContain("can't reveal");
  expect(await voice.spoken()).not.toContain('PLANT');
  await expect(page.getByText('PLANT')).toHaveCount(0);
});

test('returns the server answer only after explicit confirmation at terminal', async ({ page }) => {
  await mockWordle(page, { status: 'lost', answer: 'PLANT', guesses: [] });
  const voice = await wordleVoice(page);

  await voice.finalize({ text: 'reveal the answer', segmentId: 's1', epoch: 1 });
  expect(await voice.spoken()).not.toContain('PLANT');
  const confirmed = await voice.finalize({ text: 'yes', segmentId: 's2', epoch: 1 });
  expect(confirmed.command).toBe('confirm');
  expect(await voice.spoken()).toBe('The answer was PLANT.');
});

test('cancelling a spoiler keeps the answer hidden', async ({ page }) => {
  await mockWordle(page, { status: 'lost', answer: 'PLANT' });
  const voice = await wordleVoice(page);
  await voice.finalize({ text: 'reveal the answer', segmentId: 's1', epoch: 1 });
  const cancelled = await voice.finalize({ text: 'no', segmentId: 's2', epoch: 1 });
  expect(cancelled.command).toBe('cancel');
  expect(await voice.spoken()).toContain("won't reveal");
  expect(await voice.spoken()).not.toContain('PLANT');
});

test('quitting closes the stage without ending voice or touching the session', async ({ page }) => {
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionPosts += 1;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });
  const state = await mockWordle(page);
  const voice = await wordleVoice(page);
  await voice.finalize({ text: 'play wordle', segmentId: 's1', epoch: 1 });
  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  await expect(dialog).toBeVisible();

  const quit = await voice.finalize({ text: 'Quit Wordle', segmentId: 's2', epoch: 1 });
  expect(quit.command).toBe('quit');
  await expect(dialog).toHaveCount(0);
  expect((await voice.state()).open).toBe(false);
  expect(sessionPosts).toBe(0);
  expect(state.posts).toEqual([]);

  await voice.finalize({ text: 'play wordle', segmentId: 's3', epoch: 1 });
  await expect(dialog).toBeVisible();
  expect(state.todayCalls).toBe(1);
});

test('reports an unavailable puzzle without creating a board', async ({ page }) => {
  await mockWordle(page, { available: false, code: 'WORDLE_UNAVAILABLE' });
  const voice = await wordleVoice(page);
  const result = await voice.finalize({ text: 'play wordle', segmentId: 's1', epoch: 1 });
  expect(result.command).toBe('play');
  expect(await voice.spoken()).toContain("isn't available");
  await expect(page.getByRole('dialog', { name: 'Daily Wordle' }).getByRole('grid')).toHaveCount(0);
});

test('writes no guesses to conversation or memory endpoints', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname);
  });
  await mockWordle(page, { onGuess: () => ({ marks: ['correct', 'correct', 'correct', 'correct', 'correct'] }) });
  const voice = await wordleVoice(page);
  await voice.finalize({ text: 'play wordle', segmentId: 's1', epoch: 1 });
  await voice.finalize({ text: 'guess crane', segmentId: 's2', epoch: 1 });
  await voice.finalize({ text: 'quit wordle', segmentId: 's3', epoch: 1 });
  expect(writes.filter((path) => path.includes('/conversations') || path.includes('/memor'))).toEqual([]);
  expect(writes.filter((path) => path.includes('/wordle/'))).toEqual(['/api/v1/wordle/guesses']);
});
