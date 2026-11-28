import { expect, type Page } from '@playwright/test';
import { gotoHistory } from './historyHarness';

type FakeState = {
  connects: number;
  interrupts: number;
  ends: number;
  mutes: number;
  microphoneStarts: number;
  failMicrophone: boolean;
  genericMicrophoneFailure: boolean;
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

export async function allowMicrophone(page: Page) {
  await page.addInitScript(() => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
  });
}

export async function installFakeRuntime(page: Page) {
  await page.addInitScript(() => {
    const state: FakeState = {
      connects: 0,
      interrupts: 0,
      ends: 0,
      mutes: 0,
      microphoneStarts: 0,
      failMicrophone: false,
      genericMicrophoneFailure: false,
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
        state.microphoneStarts += 1;
        if (state.failMicrophone) {
          state.failMicrophone = false;
          throw new Error('MICROPHONE_PERMISSION_DENIED');
        }
        if (state.genericMicrophoneFailure) {
          state.genericMicrophoneFailure = false;
          throw new Error('boom');
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

export async function mockSessions(page: Page, extra: Record<string, unknown> = {}) {
  const sessions: string[] = [];
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions', async (route) => {
    const id = `sess-${sessions.length + 1}`;
    sessions.push(id);
    await route.fulfill({
      json: {
        id,
        url: 'ws://fake.invalid',
        token: 'fake',
        expires_at: new Date(Date.now() + 600_000).toISOString(),
        ...extra,
      },
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    ends.push(route.request().url());
    await route.fulfill({ json: {} });
  });
  return { sessions, ends };
}

export async function startCall(page: Page) {
  await gotoHistory(page);
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
}

export function simulate(page: Page, event: Record<string, unknown>) {
  return page.evaluate((payload) => window.__callState?.simulate(payload), event);
}
