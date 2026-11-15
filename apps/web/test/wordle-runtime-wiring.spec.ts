import { expect, test } from '@playwright/test';
import { allowMicrophone, mockConversations, mockSessions, ready } from './support/raceHarness';
import { mockWordle, type Mark } from './support/wordleHarness';

type Emitter = { emit(event: Record<string, unknown>): void; handlers: Set<unknown> };
type Hosted = { __rt?: Emitter };

/**
 * Regression for the page wiring: the runtime the shell creates when a call
 * starts must reach WordleLauncher (a non-reactive `runtime` let left it
 * undefined). The event goes through the subscriber set the launcher registered.
 */
test('a sarjy.wordle open event from the connected runtime reaches the launcher and updates the board', async ({
  page,
}) => {
  await mockConversations(page);
  await mockSessions(page);
  const state = await mockWordle(page);
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
      async say() {}
      async sendControl() {}
      async resumeAudio() {}
      microphoneStream() {
        return null;
      }
      remoteAudioLevel() {
        return 0;
      }
    }
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

  const dialog = page.getByRole('dialog', { name: 'Daily Wordle' });
  await expect(dialog).toBeHidden();
  await page.evaluate(() => (window as unknown as Hosted).__rt!.emit({ type: 'wordle', action: 'open' }));
  await expect(dialog).toBeVisible();

  const marks: Mark[] = ['absent', 'present', 'absent', 'absent', 'correct'];
  state.guesses.push({ word: 'CRANE', marks });
  await page.evaluate(() => (window as unknown as Hosted).__rt!.emit({ type: 'wordle', action: 'refresh' }));
  await expect(dialog.getByRole('gridcell', { name: 'E, correct position' })).toBeVisible();
});
