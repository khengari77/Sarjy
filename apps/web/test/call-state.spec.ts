import { expect, test, type Page } from '@playwright/test';
import { gotoHistory } from './support/historyHarness';

type FakeState = {
  connects: number;
  interrupts: number;
  ends: number;
  mutes: number;
  failMicrophone: boolean;
  handlers: Set<(event: unknown) => void>;
};

declare global {
  interface Window {
    __callState?: {
      state: FakeState;
      simulate: (event: Record<string, unknown>) => void;
    };
    __SARJY_VOICE_RUNTIME_FACTORY__?: () => unknown;
  }
}

async function allowMicrophone(page: Page) {
  await page.addInitScript(() => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
  });
}

async function installFakeRuntime(page: Page) {
  await page.addInitScript(() => {
    const state: FakeState = {
      connects: 0,
      interrupts: 0,
      ends: 0,
      mutes: 0,
      failMicrophone: false,
      handlers: new Set(),
    };
    const runtime = {
      __epoch: 0,
      get epoch() {
        return this.__epoch;
      },
      subscribe(handler: (event: unknown) => void) {
        state.handlers.add(handler);
        return () => state.handlers.delete(handler);
      },
      simulate(event: Record<string, unknown>) {
        for (const handler of [...state.handlers]) handler({ ...event, epoch: this.__epoch });
      },
      async connect() {
        state.connects += 1;
        this.__epoch += 1;
        this.simulate({ type: 'status', source: 'transport', text: 'connected' });
        this.simulate({ type: 'status', source: 'agent', text: 'listening' });
      },
      async disconnect() {
        state.ends += 1;
        this.__epoch += 1;
      },
      async cancelConnect() {
        this.__epoch += 1;
      },
      async startMicrophone() {
        if (state.failMicrophone) {
          state.failMicrophone = false;
          throw new Error('MICROPHONE_PERMISSION_DENIED');
        }
      },
      async stopMicrophone() {
        state.mutes += 1;
      },
      async sendText() {},
      async resumeAudio() {},
      async interrupt() {
        state.interrupts += 1;
        this.simulate({ type: 'status', source: 'agent', text: 'listening' });
      },
      microphoneStream() {
        return null;
      },
    };
    window.__callState = { state, simulate: (event) => runtime.simulate(event) };
    window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => runtime;
  });
}

async function mockSessions(page: Page) {
  const sessions: string[] = [];
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions', async (route) => {
    const id = `sess-${sessions.length + 1}`;
    sessions.push(id);
    await route.fulfill({
      json: { id, url: 'ws://fake.invalid', token: 'fake', expires_at: new Date(Date.now() + 600_000).toISOString() },
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    ends.push(route.request().url());
    await route.fulfill({ json: {} });
  });
  return { sessions, ends };
}

async function startCall(page: Page) {
  await gotoHistory(page);
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
}

function simulate(page: Page, event: Record<string, unknown>) {
  return page.evaluate((payload) => window.__callState?.simulate(payload), event);
}

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('a denied unmute returns to listening after a successful microphone retry', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await page.getByRole('button', { name: 'Mute', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unmute microphone', exact: true })).toBeVisible();

  await page.evaluate(() => {
    if (window.__callState) window.__callState.state.failMicrophone = true;
  });
  await page.getByRole('button', { name: 'Unmute microphone', exact: true }).click();

  const notice = page.locator('.voice-stage [data-code="MICROPHONE_PERMISSION_DENIED"]');
  await expect(notice).toContainText('Microphone access is blocked');
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'listening');

  await page.getByRole('button', { name: 'Retry microphone', exact: true }).click();
  await expect(notice).toHaveCount(0);
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'listening');
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  expect(ends).toHaveLength(0);
});

test('a recoverable error on a live call keeps the normal actions and never reconnects', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'error', code: 'TRANSCRIPT_UNAVAILABLE' });
  await expect(page.locator('.voice-stage [data-code="TRANSCRIPT_UNAVAILABLE"]')).toContainText(
    'The transcript could not be read',
  );
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'listening');
  await expect(page.getByRole('button', { name: 'Mute', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await simulate(page, { type: 'status', source: 'agent', text: 'speaking' });
  await expect(page.getByRole('button', { name: 'Interrupt', exact: true })).toBeVisible();
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'speaking');

  await page.locator('.orb-button').click();
  await expect.poll(() => page.evaluate(() => window.__callState?.state.interrupts)).toBe(1);
  expect(ends).toHaveLength(0);
  await expect(page.locator('[data-code="CONNECTION_LOST"]')).toHaveCount(0);
});

test('a lost connection shows Offline and never the Connected pill', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'status', source: 'transport', text: 'disconnected' });
  await expect(page.locator('.status-line')).toContainText('Offline');
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'disconnected');
  await expect(page.locator('.connected-pill')).toHaveCount(0);
  await expect(page.locator('[data-code="CONNECTION_LOST"]')).toContainText('Connection lost');
});

test('the stage status lines announce nothing themselves', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await expect(page.locator('.voice-stage [role="status"]')).toHaveCount(0);
  await simulate(page, { type: 'status', source: 'agent', text: 'thinking' });
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'thinking');
  await expect(page.locator('.voice-stage [role="status"]')).toHaveCount(0);
});

test('blocked audio playback explains the Enable audio button', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'audio-blocked', code: 'AUDIO_PLAYBACK_BLOCKED' });
  const enable = page.getByRole('button', { name: 'Enable audio', exact: true });
  await expect(enable).toBeVisible();
  await expect(page.getByText('The browser blocked audio playback. Select “Enable audio” to continue.')).toBeVisible();
});
