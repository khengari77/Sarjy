import { expect, test, type Page } from '@playwright/test';
import { allowMicrophone, mockConversations, mockSessions, ready } from './support/raceHarness';
import { mockWordle, type Mark } from './support/wordleHarness';

type Emitter = { emit(event: Record<string, unknown>): void; handlers: Set<unknown> };
type Hosted = { __rt?: Emitter; __controls?: unknown[]; __said?: string[] };

/**
 * Regression for the page wiring: the runtime the shell creates when a call
 * starts must reach WordleLauncher (a non-reactive `runtime` let left it
 * undefined). The event goes through the subscriber set the launcher registered.
 */
async function connected(page: Page, options: Parameters<typeof mockWordle>[1] = {}) {
  await mockConversations(page);
  await mockSessions(page);
  const state = await mockWordle(page, options);
  await page.route('**/api/v1/voice/sessions', async (route) => {
    await route.fulfill({
      json: {
        id: 's1',
        url: 'ws://fake.invalid',
        token: 't',
        expires_at: new Date(Date.now() + 600_000).toISOString(),
      },
    });
  });
  await allowMicrophone(page);
  await page.addInitScript(() => {
    const hosted = window as unknown as Hosted & { __SARJY_VOICE_RUNTIME_FACTORY__: () => unknown };
    class Runtime {
      handlers = new Set<(event: unknown) => void>();
      epoch = 1;
      subscribe(handler: (event: unknown) => void) {
        this.handlers.add(handler);
        return () => this.handlers.delete(handler);
      }
      emit(event: Record<string, unknown>) {
        for (const handler of [...this.handlers]) handler({ ...event, epoch: this.epoch });
      }
      async connect() {
        queueMicrotask(() => {
          this.emit({ type: 'status', source: 'transport', text: 'connected' });
          this.emit({ type: 'status', source: 'agent', text: 'listening' });
        });
      }
      async startMicrophone() {}
      async stopMicrophone() {}
      async disconnect() {}
      async cancelConnect() {}
      async interrupt() {}
      async sendText() {}
      async say(text: string) {
        hosted.__said?.push(text);
      }
      async sendControl(event: unknown) {
        hosted.__controls?.push(event);
      }
      async resumeAudio() {}
      microphoneStream() {
        return null;
      }
      remoteAudioLevel() {
        return 0;
      }
    }
    hosted.__controls = [];
    hosted.__said = [];
    hosted.__SARJY_VOICE_RUNTIME_FACTORY__ = () => (hosted.__rt = new Runtime() as unknown as Emitter);
  });

  await page.goto('/');
  await ready(page);
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  // The launcher must have subscribed to this very runtime.
  await expect
    .poll(() => page.evaluate(() => (window as unknown as Hosted).__rt?.handlers.size ?? 0))
    .toBeGreaterThan(1);

  return state;
}

test('a sarjy.wordle open event from the connected runtime reaches the launcher and updates the board', async ({
  page,
}) => {
  const state = await connected(page);
  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  await expect(dialog).toBeHidden();
  await page.evaluate(() => (window as unknown as Hosted).__rt!.emit({ type: 'wordle', action: 'open' }));
  await expect(dialog).toBeVisible();

  const marks: Mark[] = ['absent', 'present', 'absent', 'absent', 'correct'];
  state.guesses.push({ word: 'CRANE', marks });
  await page.evaluate(() => (window as unknown as Hosted).__rt!.emit({ type: 'wordle', action: 'refresh' }));
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toBeVisible();
});

test('a spoken reset confirmed over the connected runtime resets the game and refreshes the popup', async ({
  page,
}) => {
  const state = await connected(page, {
    guesses: [{ word: 'CRANE', marks: ['absent', 'absent', 'absent', 'absent', 'absent'] }],
  });
  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  const emit = (event: Record<string, unknown>) =>
    page.evaluate((value) => (window as unknown as Hosted).__rt!.emit(value), event);
  await emit({ type: 'wordle', action: 'open' });
  await expect(dialog.getByRole('gridcell', { name: 'C, not in the word' })).toBeVisible();

  // The model's reset_wordle tool asked: the worker speaks, the client only arms the yes/no answer.
  await emit({ type: 'wordle', action: 'confirm_reset' });
  await expect
    .poll(() => page.evaluate(() => (window as unknown as Hosted).__controls))
    .toContainEqual({ type: 'wordle.pending', value: 'reset' });
  expect(state.resets).toBe(0);

  await emit({ type: 'transcript', text: 'Yes.', final: true, segmentId: 'y1', speaker: 'user' });
  await expect.poll(() => state.resets).toBe(1);
  await expect(dialog.getByRole('gridcell', { name: 'C, not in the word' })).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => (window as unknown as Hosted).__said))
    .toContain('Game reset. Six guesses left.');
});
