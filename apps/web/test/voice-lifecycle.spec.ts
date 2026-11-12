import { expect, test, type Page } from '@playwright/test';

type FakeState = {
  connects: number;
  interrupts: number;
  ends: number;
  handlers: Set<(event: unknown) => void>;
};

declare global {
  interface Window {
    __fakeRuntime?: {
      simulate: (event: Record<string, unknown>) => void;
      simulateStale: (event: Record<string, unknown>) => void;
    };
    __fakeState?: FakeState;
    __SARJY_VOICE_RUNTIME_FACTORY__?: () => unknown;
  }
}

async function allowMicrophone(page: Page) {
  await page.addInitScript(() => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream }
    });
  });
}

async function installFakeRuntime(page: Page, connectDelay = 0) {
  await page.addInitScript(
    ({ delay }) => {
      const state: FakeState = { connects: 0, interrupts: 0, ends: 0, handlers: new Set() };
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
        simulateStale(event: Record<string, unknown>) {
          for (const handler of [...state.handlers]) handler({ ...event, epoch: this.__epoch - 1 });
        },
        async connect() {
          state.connects += 1;
          this.__epoch += 1;
          this.simulate({ type: 'status', source: 'transport', text: 'connecting' });
          if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
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
        async startMicrophone() {},
        async stopMicrophone() {},
        async sendText() {},
        async resumeAudio() {},
        async interrupt() {
          state.interrupts += 1;
          this.simulate({ type: 'status', source: 'agent', text: 'listening' });
        },
        microphoneStream() {
          return null;
        }
      };
      window.__fakeState = state;
      window.__fakeRuntime = runtime;
      window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => runtime;
    },
    { delay: connectDelay }
  );
}

async function mockSessions(page: Page) {
  const sessions: string[] = [];
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions', async (route) => {
    const id = `sess-${sessions.length + 1}`;
    sessions.push(id);
    await route.fulfill({
      json: { id, url: 'ws://fake.invalid', token: 'fake', expires_at: new Date(Date.now() + 600_000).toISOString() }
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    ends.push(route.request().url());
    await route.fulfill({ json: {} });
  });
  return { sessions, ends };
}

async function waitForReady(page: Page) {
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('denied microphone shows retry copy, creates no session and leaves text usable', async ({ page }) => {
  const { sessions } = await mockSessions(page);
  await installFakeRuntime(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => Promise.reject(Object.assign(new Error('denied'), { name: 'NotAllowedError' })) }
    });
  });

  await page.goto('/');
  await waitForReady(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();

  const alert = page.locator('[data-code="MICROPHONE_PERMISSION_DENIED"]');
  await expect(alert).toBeVisible();
  await expect(alert).toContainText(/Microphone access is blocked/i);
  expect(sessions).toHaveLength(0);
  await expect(page.getByRole('textbox', { name: 'Write a message…' })).toBeEnabled();
});

test('starting and ending a session releases the exact session id', async ({ page }) => {
  const { sessions, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);

  await page.goto('/');
  await waitForReady(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  expect(sessions).toEqual(['sess-1']);

  await page.getByRole('button', { name: 'End call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  expect(ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
});

test('cancelling a pending connection ends the session it already created', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page, 1500);

  await page.goto('/');
  await waitForReady(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  const cancel = page.getByRole('button', { name: 'Cancel connecting', exact: true });
  await expect(cancel).toBeVisible();
  await cancel.click();

  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end')), { timeout: 5000 }).toBe(true);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
});

test('transport loss offers an explicit reconnect and never loops', async ({ page }) => {
  const { sessions } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);

  await page.goto('/');
  await waitForReady(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.evaluate(() =>
    window.__fakeRuntime?.simulate({ type: 'status', source: 'transport', text: 'disconnected' })
  );
  await expect(page.locator('[data-code="CONNECTION_LOST"]')).toContainText('Connection lost');
  expect(sessions).toHaveLength(1);

  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await expect.poll(() => sessions.length).toBe(2);
});

test('orb interrupts only while the agent is speaking or thinking', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);

  await page.goto('/');
  await waitForReady(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.evaluate(() =>
    window.__fakeRuntime?.simulate({ type: 'status', source: 'agent', text: 'speaking' })
  );
  await page.getByRole('button', { name: 'Speaking. Interrupt Sarjy' }).click();
  await expect.poll(() => page.evaluate(() => window.__fakeState?.interrupts)).toBe(1);
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
});

test('events from a replaced session epoch are ignored', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);

  await page.goto('/');
  await waitForReady(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.evaluate(() =>
    window.__fakeRuntime?.simulateStale({ type: 'error', code: 'DAILY_BUDGET_EXHAUSTED' })
  );
  await expect(page.locator('[data-code="DAILY_BUDGET_EXHAUSTED"]')).toHaveCount(0);
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
});
