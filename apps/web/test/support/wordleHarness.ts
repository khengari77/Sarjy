import { expect, type Page } from '@playwright/test';

export type Mark = 'correct' | 'present' | 'absent';
export type Guess = { word: string; marks: Mark[] };
export type Status = 'in_progress' | 'won' | 'lost';

export const DATE = '2026-11-13';
export const STATS = { played: 4, won: 3, current_streak: 1, max_streak: 2 };

export interface Options {
  available?: boolean;
  code?: string;
  status?: Status;
  guesses?: Guess[];
  answer?: string;
  onGuess?: (word: string) => { marks: Mark[]; status?: Status; answer?: string; code?: string };
  statsCode?: number;
  /** Delay the today response to exercise input races during loading. */
  todayDelayMs?: number;
  /** Adversarial fixture: send an answer while the game is still in progress. */
  leakAnswer?: boolean;
}

/** Mirrors the #108 route shape: the terminal answer lives inside `game`. */
export async function mockWordle(page: Page, options: Options = {}) {
  const state = {
    available: options.available ?? true,
    code: options.code,
    status: options.status ?? 'in_progress',
    guesses: [...(options.guesses ?? [])],
    answer: options.answer,
    posts: [] as { guess: string; request_id: string }[],
    todayCalls: 0,
    statsCalls: 0,
  };

  function game() {
    return {
      id: 'game-1',
      date: DATE,
      status: state.status,
      attempts_remaining: 6 - state.guesses.length,
      guesses: state.guesses,
      ...((state.status !== 'in_progress' || options.leakAnswer) && state.answer ? { answer: state.answer } : {}),
    };
  }

  await page.route('**/api/v1/wordle/today', async (route) => {
    state.todayCalls += 1;
    if (options.todayDelayMs) await new Promise((resolve) => setTimeout(resolve, options.todayDelayMs));
    await route.fulfill({
      json: state.available
        ? { date: DATE, available: true, game: game() }
        : { date: DATE, available: false, game: null, code: state.code ?? 'WORDLE_UNAVAILABLE' },
    });
  });

  await page.route('**/api/v1/wordle/guesses', async (route) => {
    const body = route.request().postDataJSON() as { guess: string; request_id: string };
    state.posts.push(body);
    const result = options.onGuess?.(body.guess) ?? {
      marks: ['absent', 'absent', 'absent', 'absent', 'absent'] as Mark[],
    };
    if (result.code) {
      await route.fulfill({ status: 422, json: { detail: { code: result.code } } });
      return;
    }
    state.guesses.push({ word: body.guess, marks: result.marks });
    state.status = result.status ?? state.status;
    state.answer = result.answer ?? state.answer;
    await route.fulfill({ json: { ...game(), marks: result.marks } });
  });

  await page.route('**/api/v1/wordle/stats', async (route) => {
    state.statsCalls += 1;
    if (options.statsCode) {
      await route.fulfill({ status: options.statsCode, json: { detail: { code: 'WORDLE_UNAVAILABLE' } } });
      return;
    }
    await route.fulfill({ json: STATS });
  });

  await page.route('**/api/v1/voice/sessions', async (route) => {
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });
  return state;
}

export async function openWordle(page: Page, theme?: 'dark' | 'light') {
  await page.goto('/');
  if (theme) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
  }
  const entry = page.locator('.wordle-entry:visible');
  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  // Retry the click until hydration has attached the launcher handler; the
  // reduced-motion render tier does not expose the usual orb hydration flag.
  await expect(async () => {
    await entry.click();
    await expect(dialog).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 20_000 });
  // Opening is not the same as being ready: today's puzzle must settle before
  // keyboard input is accepted, otherwise a slow route silently eats guesses.
  await expect(dialog).not.toHaveAttribute('data-view', 'loading');
  return dialog;
}

export interface VoiceDebugResult {
  handled: boolean;
  command?: string;
  spoken?: string;
  ignored?: string;
}

export interface VoiceDebugInput {
  text: string;
  segmentId?: string;
  epoch?: number;
  final?: boolean;
  speaker?: 'user' | 'assistant';
}

export interface VoiceDebug {
  finalize(input: VoiceDebugInput): Promise<VoiceDebugResult>;
  spoken(): Promise<string>;
  state(): Promise<{ view: string; open: boolean; guesses: number }>;
}

/** Drive the DEV-only finalized-transcript seam without a live LiveKit room. */
export async function wordleVoice(page: Page): Promise<VoiceDebug> {
  if (!page.url().startsWith('http')) await page.goto('/');
  await page.waitForFunction(() => Boolean((window as unknown as { __sarjyWordleVoice?: unknown }).__sarjyWordleVoice));
  return {
    finalize: (input: VoiceDebugInput) =>
      page.evaluate(
        (value) =>
          (
            window as unknown as {
              __sarjyWordleVoice: { finalize(input: VoiceDebugInput): Promise<VoiceDebugResult> };
            }
          ).__sarjyWordleVoice.finalize(value),
        input,
      ),
    spoken: () =>
      page.evaluate(() =>
        (window as unknown as { __sarjyWordleVoice: { spoken(): string } }).__sarjyWordleVoice.spoken(),
      ),
    state: () =>
      page.evaluate(() =>
        (
          window as unknown as {
            __sarjyWordleVoice: { state(): { view: string; open: boolean; guesses: number } };
          }
        ).__sarjyWordleVoice.state(),
      ),
  };
}
