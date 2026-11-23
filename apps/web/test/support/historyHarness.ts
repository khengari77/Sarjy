import { expect, type Locator, type Page } from '@playwright/test';

export const CHATS = [
  { id: 'c1', title: 'Trip to Paris', revision: 0 },
  { id: 'c2', title: 'ميزانية Tripoli', revision: 0 },
  { id: 'c3', title: 'Work ideas', revision: 0 },
];

export async function mockHistory(page: Page, chats = CHATS) {
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

export async function mockSessions(page: Page) {
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions', async (route) => {
    await route.fulfill({
      json: {
        id: 'sess-1',
        url: 'ws://fake.invalid',
        token: 'fake',
        expires_at: new Date(Date.now() + 600_000).toISOString(),
      },
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    ends.push(route.request().url());
    await route.fulfill({ json: {} });
  });
  return ends;
}

/** The shared machine can drop hydration or return a 500 during a cold vite
 *  compile. Wait for the hydrated shell, reloading if the module graph never
 *  took over, before asserting on real history rows. */
export async function gotoHistory(page: Page) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const ready = await page
      .locator('.orb-button[data-renderer]:not([data-renderer="static"])')
      .waitFor({ timeout: 15_000 })
      .then(
        () => true,
        () => false,
      );
    if (ready) return;
  }
}

/** The selected row paints through a background transition, so assert the colour
 *  the transition ends on, not the first frame it passes through. Call this with
 *  the pointer off the row: hover overrides the selection background. */
export async function expectSelectionBackground(item: Locator) {
  const expected = await item.evaluate((element) => {
    const probe = document.createElement('span');
    probe.style.background = getComputedStyle(element).getPropertyValue('--surface-raised');
    element.parentElement?.append(probe);
    const colour = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return colour;
  });
  await expect.poll(() => item.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(expected);
}
