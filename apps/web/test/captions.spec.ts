import { expect, test, type Page } from '@playwright/test';

const CHAT = { id: 'c1', title: 'Riyadh weather', revision: 0 };
const TURNS = [
  { id: 't1', role: 'user', text: 'What is the weather in Riyadh?', status: 'completed' },
  { id: 't2', role: 'assistant', text: 'It is 31°C and sunny.', status: 'completed' },
];

async function mockData(
  page: Page,
  turns = TURNS,
  tools = [{ id: 'tc1', name: 'get_weather', result: { city: 'Riyadh' } }],
) {
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) => route.fulfill({ json: [CHAT] }));
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: turns }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: tools }));
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
    class FakeRuntime {
      __epoch = 0;
      handlers = new Set<(event: unknown) => void>();
      get epoch() {
        return this.__epoch;
      }
      subscribe(handler: (event: unknown) => void) {
        this.handlers.add(handler);
        return () => this.handlers.delete(handler);
      }
      simulate(event: Record<string, unknown>) {
        for (const handler of [...this.handlers]) handler({ ...event, epoch: this.__epoch });
      }
      async connect() {
        this.__epoch += 1;
        this.simulate({ type: 'status', source: 'transport', text: 'connected' });
        this.simulate({ type: 'status', source: 'agent', text: 'listening' });
      }
      async disconnect() {}
      async cancelConnect() {}
      async startMicrophone() {}
      async stopMicrophone() {}
      async sendText() {}
      async resumeAudio() {}
      async interrupt() {}
      microphoneStream() {
        return null;
      }
    }
    const runtime = new FakeRuntime();
    (window as unknown as { __fakeRuntime: unknown }).__fakeRuntime = runtime;
    window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => runtime;
  });
}

async function waitForReady(page: Page) {
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
}

async function startCall(page: Page) {
  await page.route('**/api/v1/voice/sessions', (route) =>
    route.fulfill({
      json: {
        id: 'sess-1',
        url: 'ws://fake.invalid',
        token: 'fake',
        expires_at: new Date(Date.now() + 600_000).toISOString(),
      },
    }),
  );
  await page.route('**/api/v1/voice/sessions/*/end', (route) => route.fulfill({ json: {} }));
  await waitForReady(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('collapsed captions show two lines, keep the orb and open a transcript', async ({ page }) => {
  await mockData(page);
  await page.goto('/');

  await expect(page.locator('.orb-button')).toBeVisible();
  await expect(page.locator('.caption.user')).toContainText('What is the weather in Riyadh?');
  await expect(page.locator('.caption.assistant')).toContainText('31°C and sunny');
  await expect(page.locator('.caption')).toHaveCount(2);

  await page.getByRole('button', { name: 'Show full transcript' }).click();
  const drawer = page.getByRole('dialog', { name: 'Conversation transcript' });
  await expect(drawer).toBeVisible();
  await expect(page.locator('.orb-button')).toBeVisible();
  await expect(drawer).toContainText('What is the weather in Riyadh?');
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
});

test('the state line sits below the orb and exposes only human wording', async ({ page }) => {
  await mockData(page);
  await page.goto('/');

  const orb = page.locator('.orb-button');
  const status = page.locator('.status-line');
  await expect(status).toHaveAttribute('data-phase', 'idle');
  await expect(status).toHaveText('Ready');
  const orbBox = await orb.boundingBox();
  const statusBox = await status.boundingBox();
  expect(orbBox).not.toBeNull();
  expect(statusBox).not.toBeNull();
  expect(statusBox!.y).toBeGreaterThan(orbBox!.y + orbBox!.height - 1);
  // The visible line must not be its own live region; the page owns one
  // debounced announcer for screen readers.
  await expect(status).not.toHaveAttribute('role', 'status');
});

test('the transcript uses human role labels rather than raw API enums', async ({ page }) => {
  await mockData(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Show full transcript' }).click();
  const drawer = page.getByRole('dialog', { name: 'Conversation transcript' });
  await expect(drawer).toContainText('You');
  await expect(drawer).toContainText('Sarjy');
  await expect(drawer).not.toContainText('user · completed');
  await expect(drawer).not.toContainText('assistant · completed');
});

test('opening the drawer focuses its close control and returns focus', async ({ page }) => {
  await mockData(page);
  await page.goto('/');
  const expand = page.getByRole('button', { name: 'Show full transcript' });
  await expand.click();
  await expect(page.getByRole('button', { name: 'Close transcript' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(expand).toBeFocused();
});

test('partial text never reaches the debounced live region', async ({ page }) => {
  await mockData(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await page.goto('/');
  await startCall(page);

  const announcer = page.locator('.status-announcer');
  await expect(announcer).toHaveText('Listening…');

  await page.evaluate(() =>
    (window as unknown as { __fakeRuntime: { simulate: (event: unknown) => void } }).__fakeRuntime.simulate({
      type: 'transcript',
      text: 'unfinished phrase',
      final: false,
      segmentId: 'seg-live',
      speaker: 'user',
    }),
  );
  await expect(page.locator('.caption.provisional')).toContainText('unfinished phrase');
  // A partial is display-only: it must not advance the turn list or the phase.
  await expect(page.locator('.caption')).toHaveCount(2);
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  await expect(announcer).toHaveText('Listening…');
  await expect(announcer).not.toContainText('unfinished phrase');
});

test('tool results render as a compact chip, never raw payloads', async ({ page }) => {
  await mockData(page);
  await page.goto('/');
  const chip = page.getByText('Checked weather · Riyadh');
  await expect(chip).toBeVisible();
  await expect(page.locator('pre')).toHaveCount(0);
});

test('provisional transcript replaces the final line and clears without commands', async ({ page }) => {
  await mockData(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await page.goto('/');
  await startCall(page);

  await page.evaluate(() =>
    (window as unknown as { __fakeRuntime: { simulate: (event: unknown) => void } }).__fakeRuntime.simulate({
      type: 'transcript',
      text: 'It is thirty',
      final: false,
      segmentId: 'seg-1',
      speaker: 'assistant',
    }),
  );
  const provisional = page.locator('.caption.provisional');
  await expect(provisional).toContainText('It is thirty');
  await expect(page.locator('.caption')).toHaveCount(2);
  // Provisional styling must not fade text below WCAG AA contrast.
  await expect(provisional).toHaveCSS('opacity', '1');

  await page.evaluate(() =>
    (window as unknown as { __fakeRuntime: { simulate: (event: unknown) => void } }).__fakeRuntime.simulate({
      type: 'transcript',
      text: 'It is thirty one degrees.',
      final: true,
      segmentId: 'seg-1',
      speaker: 'assistant',
    }),
  );
  await expect(page.locator('.caption.provisional')).toHaveCount(0);
});

test('turning captions off keeps the session and the transcript reachable', async ({ page }) => {
  await mockData(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await page.goto('/');
  await startCall(page);

  await page.getByRole('checkbox', { name: 'Show captions' }).uncheck();
  await expect(page.locator('.captions')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Transcript', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Transcript', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Conversation transcript' })).toBeVisible();

  // Turning captions back on must repaint from stored turns, not a fresh call.
  await page.keyboard.press('Escape');
  await page.getByRole('checkbox', { name: 'Show captions' }).check();
  await expect(page.locator('.caption.user')).toContainText('What is the weather in Riyadh?');
});

test('a live session with no turns still exposes the transcript', async ({ page }) => {
  await mockData(page, []);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await page.goto('/');
  await startCall(page);

  const trigger = page.getByRole('button', { name: 'Transcript', exact: true });
  await expect(trigger).toBeVisible();
  await trigger.click();
  const drawer = page.getByRole('dialog', { name: 'Conversation transcript' });
  await expect(drawer).toContainText('No messages yet.');
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
});

test('a voice-catalog failure stays in the settings panel and leaves the orb live', async ({ page }) => {
  await mockData(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await page.route('**/api/v1/voices?*', (route) =>
    route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } }),
  );
  await page.goto('/');
  await startCall(page);

  await page.getByRole('button', { name: 'Load voices / Search', exact: true }).click();
  await expect(page.locator('.voice-error')).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  await expect(page.locator('[data-code="API_UNAVAILABLE"]')).toHaveCount(0);
});
