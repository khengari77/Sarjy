import { expect, test, type Page } from '@playwright/test';

const CHAT = { id: 'c1', title: 'Trip to Paris', revision: 0 };

async function mockApp(page: Page) {
  const sessionBodies: Record<string, unknown>[] = [];
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: [CHAT] })
      : route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } })
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({
      json: { id: 'sess-1', url: 'ws://fake.invalid', token: 'fake', expires_at: new Date(Date.now() + 600_000).toISOString() }
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', (route) => route.fulfill({ json: {} }));
  return { sessionBodies };
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
      emit(event: Record<string, unknown>) {
        for (const handler of [...this.handlers]) handler({ ...event, epoch: this.__epoch });
      }
      async connect() {
        this.__epoch += 1;
        this.emit({ type: 'status', source: 'transport', text: 'connected' });
        this.emit({ type: 'status', source: 'agent', text: 'listening' });
      }
      async disconnect() {
        this.__epoch += 1;
      }
      async cancelConnect() {}
      async startMicrophone() {}
      async stopMicrophone() {}
      async sendText() {}
      async resumeAudio() {}
      async interrupt() {}
      microphoneStream() {
        return null;
      }
      remoteAudioLevel() {
        return 0;
      }
    }
    window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => new FakeRuntime();
  });
}

test('a saved Arabic preference cannot start an Arabic session or reveal the switch', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'ar'));
  const mocked = await mockApp(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);

  await page.goto('/');

  await expect(page.getByRole('heading', { name: 'Speak. Remember. Shape the context.' })).toBeVisible();
  await expect(page.locator('.shell')).toHaveAttribute('dir', 'ltr');
  await expect(page.getByRole('button', { name: 'العربية' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'English' })).toHaveCount(0);
  // The page shell is server-rendered; wait for hydration before clicking.
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');

  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toContainText('Listening');

  expect(mocked.sessionBodies).toHaveLength(1);
  expect(mocked.sessionBodies[0].language).toBe('en');
  expect(mocked.sessionBodies[0].conversation_id).toBe('c1');
});

test('a fresh browser defaults to the English interface', async ({ page }) => {
  await mockApp(page);
  await page.goto('/');

  await expect(page.getByRole('heading', { name: 'Speak. Remember. Shape the context.' })).toBeVisible();
  await expect(page.locator('.shell')).toHaveAttribute('dir', 'ltr');
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeEnabled();
});
