import { expect, test, type Page } from '@playwright/test';

const CHATS = [
  { id: 'c1', title: 'Chat A', revision: 0 },
  { id: 'c2', title: 'Chat B', revision: 0 }
];

declare global {
  interface Window {
    __race?: {
      autoConnect: boolean;
      startMicFail: boolean;
      connects: string[];
      disconnects: number;
      cancels: number;
      sent: string[];
      resolveConnect(id: string): void;
      stats(): { connects: string[]; disconnects: number; cancels: number; sent: string[] };
    };
    __permission?: { resolve(): void; reject(): void };
    __SARJY_VOICE_RUNTIME_FACTORY__?: () => unknown;
  }
}

async function mockConversations(page: Page) {
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: CHATS })
      : route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } })
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
}

async function mockSessions(page: Page) {
  const posts: { id: string; body: Record<string, unknown> }[] = [];
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions', async (route) => {
    const id = `sess-${posts.length + 1}`;
    posts.push({ id, body: route.request().postDataJSON() as Record<string, unknown> });
    await route.fulfill({
      json: { id, url: 'ws://fake.invalid', token: 'fake', expires_at: new Date(Date.now() + 600_000).toISOString() }
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    ends.push(route.request().url());
    await route.fulfill({ json: {} });
  });
  return { posts, ends };
}

async function installRaceRuntime(page: Page, options: { autoConnect?: boolean; startMicFail?: boolean } = {}) {
  await page.addInitScript(
    ({ autoConnect, startMicFail }) => {
      const race = {
        autoConnect,
        startMicFail,
        connects: [] as string[],
        disconnects: 0,
        cancels: 0,
        sent: [] as string[],
        pending: new Map<string, () => void>(),
        resolveConnect(id: string) {
          this.pending.get(id)?.();
        },
        stats() {
          return {
            connects: [...this.connects],
            disconnects: this.disconnects,
            cancels: this.cancels,
            sent: [...this.sent]
          };
        }
      };

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
        connect(config: { id: string }) {
          const epoch = ++this.__epoch;
          race.connects.push(config.id);
          return new Promise<void>((resolve) => {
            const finish = () => {
              if (this.__epoch === epoch) {
                this.emit({ type: 'status', source: 'transport', text: 'connected' });
                this.emit({ type: 'status', source: 'agent', text: 'listening' });
              }
              resolve();
            };
            race.pending.set(config.id, finish);
            if (race.autoConnect) queueMicrotask(finish);
          });
        }
        async disconnect() {
          race.disconnects += 1;
          this.__epoch += 1;
        }
        async cancelConnect() {
          race.cancels += 1;
          this.__epoch += 1;
        }
        async startMicrophone() {
          if (race.startMicFail) throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
        }
        async stopMicrophone() {}
        async sendText(text: string) {
          race.sent.push(text);
        }
        async resumeAudio() {}
        async interrupt() {}
        microphoneStream() {
          return null;
        }
        remoteAudioLevel() {
          return 0;
        }
      }

      window.__race = race;
      window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => new FakeRuntime();
    },
    { autoConnect: options.autoConnect ?? false, startMicFail: options.startMicFail ?? false }
  );
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

async function deferMicrophone(page: Page) {
  await page.addInitScript(() => {
    const permission = { resolve: () => {}, reject: () => {} };
    const pending = new Promise<MediaStream>((resolve, reject) => {
      permission.resolve = () => resolve({ getTracks: () => [] } as unknown as MediaStream);
      permission.reject = () => reject(Object.assign(new Error('denied'), { name: 'NotAllowedError' }));
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: () => pending }
    });
    window.__permission = permission;
  });
}

async function ready(page: Page) {
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
}

test('switching conversations while connecting cancels the attempt instead of binding text to it', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page);

  await page.goto('/');
  await ready(page);
  await expect(page.getByRole('button', { name: 'Chat A', exact: true })).toHaveAttribute('aria-current', 'page');

  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].body.conversation_id).toBe('c1');

  await page.getByRole('button', { name: 'Chat B', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Chat B', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toHaveCount(0);

  // Resolve the abandoned attempt after the switch. It may only release its
  // own session; it must not become live or send text to the selected chat.
  await page.evaluate(() => window.__race?.resolveConnect('sess-1'));
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.__race?.stats())).toMatchObject({ connects: ['sess-1'], sent: [] });
});

test('a cancelled attempt cannot disconnect its replacement', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page);

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel connecting', exact: true }).click();

  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect.poll(() => posts.length).toBe(2);
  await page.evaluate(() => window.__race?.resolveConnect('sess-2'));
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');

  // The first attempt resolves after the replacement is live. Its cleanup must
  // leave the replacement runtime and session alone.
  await page.evaluate(() => window.__race?.resolveConnect('sess-1'));
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  expect(ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  expect(ends.some((url) => url.endsWith('/sess-2/end'))).toBe(false);
  expect(await page.evaluate(() => window.__race?.stats())).toMatchObject({
    connects: ['sess-1', 'sess-2'],
    disconnects: 0,
    cancels: 1
  });
});

test('cancelling while microphone permission is pending creates no session', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await deferMicrophone(page);
  await installRaceRuntime(page);

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel connecting', exact: true })).toBeVisible();
  expect(posts).toHaveLength(0);

  await page.getByRole('button', { name: 'Cancel connecting', exact: true }).click();
  await page.evaluate(() => window.__permission?.resolve());
  await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 250)));

  expect(posts).toHaveLength(0);
  expect(ends).toHaveLength(0);
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  expect((await page.evaluate(() => window.__race?.stats()))?.cancels).toBe(1);
});

test('microphone startup failure clears the ended session and allows retry', async ({ page }) => {
  await mockConversations(page);
  const { posts, ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installRaceRuntime(page, { autoConnect: true, startMicFail: true });

  await page.goto('/');
  await ready(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();

  await expect(page.locator('[data-code="MICROPHONE_PERMISSION_DENIED"]')).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  await expect(page.locator('.orb-button')).toHaveAttribute('data-output-level', '0.00');

  const retry = page.getByRole('button', { name: 'Retry microphone', exact: true });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect.poll(() => posts.length).toBe(2);
});
