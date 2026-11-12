import { expect, test, type Page } from '@playwright/test';

const CHATS = [
  { id: 'c1', title: 'Trip to Paris', revision: 0 },
  { id: 'c2', title: 'ميزانية Tripoli', revision: 0 },
  { id: 'c3', title: 'Work ideas', revision: 0 }
];

async function mockHistory(page: Page, chats = CHATS) {
  let listCalls = 0;
  let created = 0;
  const turns: string[] = [];
  await page.route('**/api/v1/conversations', async (route) => {
    if (route.request().method() === 'GET') {
      listCalls += 1;
      await route.fulfill({ json: chats });
    } else {
      created += 1;
      await route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } });
    }
  });
  await page.route('**/api/v1/conversations/*/turns', async (route) => {
    turns.push(route.request().url());
    await route.fulfill({ json: [] });
  });
  await page.route('**/api/v1/conversations/*/tool-calls', async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.route('**/api/v1/demo-session', async (route) => {
    await route.fulfill({ json: { id: 'owner' } });
  });
  return { listCalls: () => listCalls, created: () => created, turns };
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
    }
    window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => new FakeRuntime();
  });
}

async function mockSessions(page: Page) {
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions', async (route) => {
    await route.fulfill({
      json: { id: 'sess-1', url: 'ws://fake.invalid', token: 'fake', expires_at: new Date(Date.now() + 600_000).toISOString() }
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    ends.push(route.request().url());
    await route.fulfill({ json: {} });
  });
  return ends;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('search filters by title client-side and reports no matches', async ({ page }) => {
  const mocked = await mockHistory(page);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible();

  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('paris');
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Work ideas', exact: true })).toHaveCount(0);
  expect(mocked.listCalls()).toBe(1);

  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('zzz');
  await expect(page.getByText('No matching conversations.')).toBeVisible();
});

test('Arabic titles match without diacritics or hamza variants', async ({ page }) => {
  await mockHistory(page);
  await page.goto('/');
  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('ميزانيه');
  await expect(page.getByRole('button', { name: 'ميزانية Tripoli', exact: true })).toBeVisible();
});

test('resuming a conversation loads turns without starting a microphone session', async ({ page }) => {
  const mocked = await mockHistory(page);
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionPosts += 1;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Work ideas', exact: true }).click();
  await expect.poll(() => mocked.turns.length).toBeGreaterThanOrEqual(2);
  expect(sessionPosts).toBe(0);
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeEnabled();
});

test('an empty list offers an explicit new-chat action', async ({ page }) => {
  await mockHistory(page, []);
  await page.goto('/');
  await expect(page.getByText('No conversations yet.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start a conversation' })).toBeVisible();
});

test('a list failure stays inside the sidebar and leaves the orb idle', async ({ page }) => {
  await page.route('**/api/v1/conversations', async (route) => {
    await route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
  });
  await page.route('**/api/v1/demo-session', async (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.goto('/');
  await expect(page.locator('[data-code="API_UNAVAILABLE"]')).toContainText('Could not load conversations');
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'idle');
});

test('switching chats mid-call requires an explicit end and switch', async ({ page }) => {
  await mockHistory(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  const ends = await mockSessions(page);

  await page.goto('/');
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Work ideas', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'End the current voice session?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  expect(ends).toHaveLength(0);

  await page.getByRole('button', { name: 'Work ideas', exact: true }).click();
  await page.getByRole('button', { name: 'End & switch' }).click();
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
});

test('starting a new conversation mid-call requires the same confirmation', async ({ page }) => {
  const mocked = await mockHistory(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await mockSessions(page);

  await page.goto('/');
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'New conversation +' }).click();
  const dialog = page.getByRole('dialog', { name: 'End the current voice session?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(mocked.created()).toBe(0);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'New conversation +' }).click();
  await page.getByRole('button', { name: 'End & start' }).click();
  await expect.poll(() => mocked.created()).toBe(1);
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
});

test('rename and delete are disabled with an explanation', async ({ page }) => {
  await mockHistory(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Conversation actions for Trip to Paris' }).click();
  const menu = page.getByRole('menu');
  await expect(menu.getByRole('menuitem', { name: 'Rename' })).toBeDisabled();
  await expect(menu.getByRole('menuitem', { name: 'Delete' })).toBeDisabled();
  await expect(menu).toContainText('Rename and delete need backend support.');
});
