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
  await page.route('**/api/v1/settings', (route) =>
    route.fulfill({
      json: {
        speaking_speed: 1,
        captions_visible: true,
        approved_voice_id: null,
        response_preference: 'automatic',
        revision: 0,
      },
    }),
  );
  await page.route('**/api/v1/settings/actions', async (route) => {
    const body = route.request().postDataJSON() as { action: { kind: string; visible?: boolean } };
    await route.fulfill({
      json: {
        kind: body.action.kind,
        settings: {
          speaking_speed: 1,
          captions_visible: body.action.visible ?? true,
          approved_voice_id: null,
          response_preference: 'automatic',
          revision: 1,
        },
        changed: true,
      },
    });
  });
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
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('the caption shows one quoted assistant line and opens the transcript tab', async ({ page }) => {
  await mockData(page);
  await page.goto('/');

  await expect(page.locator('.orb-button')).toBeVisible();
  const bubble = page.locator('.caption-bubble');
  await expect(bubble).toHaveCount(1);
  await expect(bubble).toContainText('It is 31°C and sunny.');
  await expect(bubble).not.toContainText('What is the weather');
  // The old overlay panel and the separate stage button are gone.
  await expect(page.getByRole('dialog', { name: 'Conversation transcript' })).toHaveCount(0);
  await expect(page.locator('.transcript-trigger')).toHaveCount(0);

  await bubble.focus();
  await page.keyboard.press('Enter');
  const drawer = page.locator('#shell-right');
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole('tab', { name: 'Transcript' })).toHaveAttribute('aria-selected', 'true');
  const panel = page.locator('#drawer-panel-transcript');
  await expect(panel).toContainText('What is the weather in Riyadh?');
  // Rows are labelled by speaker; completed turns carry no status.
  await expect(panel.locator('.turn').nth(0).locator('small')).toHaveText('You');
  await expect(panel.locator('.turn').nth(1).locator('small')).toHaveText('Sarjy');
  await expect(panel).not.toContainText('completed');
  await expect(panel).not.toContainText('user ·');

  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(bubble).toBeFocused();
});

test('only interrupted or failed turns show a status in the transcript', async ({ page }) => {
  await mockData(page, [
    { id: 't1', role: 'user', text: 'Tell me a story', status: 'completed' },
    { id: 't2', role: 'assistant', text: 'Once upon a', status: 'interrupted' },
    { id: 't3', role: 'assistant', text: 'Sorry', status: 'failed' },
  ]);
  await page.goto('/');
  await page.locator('.caption-bubble').click();
  const panel = page.locator('#drawer-panel-transcript');
  await expect(panel.locator('.turn').nth(0).locator('small')).toHaveText('You');
  await expect(panel.locator('.turn').nth(1).locator('small')).toHaveText('Sarjy · Interrupted');
  await expect(panel.locator('.turn').nth(2).locator('small')).toHaveText('Sarjy · Failed');
});

test('tool results render only in the transcript tab, never on the stage or as raw payloads', async ({ page }) => {
  await mockData(page);
  await page.goto('/');

  // Audit detail belongs in the full transcript, not under the live answer.
  await expect(page.getByText('Checked weather · Riyadh')).toHaveCount(0);
  await expect(page.locator('pre')).toHaveCount(0);

  await page.locator('.caption-bubble').click();
  const panel = page.locator('#drawer-panel-transcript');
  await expect(panel.getByText('Checked weather · Riyadh')).toBeVisible();
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
  const provisional = page.locator('.caption-bubble.provisional');
  await expect(provisional).toContainText('It is thirty');
  await expect(page.locator('.caption-bubble')).toHaveCount(1);

  await page.evaluate(() =>
    (window as unknown as { __fakeRuntime: { simulate: (event: unknown) => void } }).__fakeRuntime.simulate({
      type: 'transcript',
      text: 'It is thirty one degrees.',
      final: true,
      segmentId: 'seg-1',
      speaker: 'assistant',
    }),
  );
  await expect(page.locator('.caption-bubble.provisional')).toHaveCount(0);
  await expect(page.locator('.caption-bubble')).toContainText('It is thirty one degrees.');
});

test('turning captions off keeps the session and the transcript reachable', async ({ page }) => {
  await mockData(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await page.goto('/');
  await startCall(page);

  await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
  await page.locator('#shell-right').getByRole('tab', { name: 'Controls' }).click();
  await page.getByRole('checkbox', { name: 'Show captions' }).uncheck();
  // The quote goes away but the bubble stays as the way into the transcript.
  const bubble = page.getByRole('button', { name: 'Transcript', exact: true });
  await expect(page.locator('.caption-bubble.quiet')).toHaveCount(1);
  await expect(page.locator('.caption-bubble')).not.toContainText('31°C');
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await bubble.click();
  await expect(page.locator('#shell-right').getByRole('tab', { name: 'Transcript' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
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
  const panel = page.locator('#drawer-panel-transcript');
  await expect(panel).toContainText('No messages yet.');
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
});

test('a long reply never moves the orb or the composer', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockData(page, [
    { id: 't1', role: 'user', text: 'Hi', status: 'completed' },
    { id: 't2', role: 'assistant', text: 'Short.', status: 'completed' },
  ]);
  await page.goto('/');
  const geometry = () =>
    page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)?.getBoundingClientRect();
      return {
        orb: box('.orb-button')?.top ?? -1,
        bubble: box('.caption-bubble')?.top ?? -1,
        bubbleHeight: box('.caption-bubble')?.height ?? -1,
        composer: box('.composer')?.top ?? -1,
      };
    });
  await expect(page.locator('.caption-bubble')).toContainText('Short.');
  const before = await geometry();
  await page.route('**/api/v1/conversations/*/turns', (route) =>
    route.fulfill({
      json: [
        { id: 't1', role: 'user', text: 'Hi', status: 'completed' },
        { id: 't2', role: 'assistant', text: 'A very long reply. '.repeat(60), status: 'completed' },
      ],
    }),
  );
  await page.reload();
  await expect(page.locator('.caption-bubble')).toContainText('A very long reply.');
  expect(await geometry()).toEqual(before);
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

  await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
  await page.getByRole('button', { name: 'Load voices / Search', exact: true }).click();
  await expect(page.locator('.voice-error')).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  await expect(page.locator('[data-code="API_UNAVAILABLE"]')).toHaveCount(0);
});
