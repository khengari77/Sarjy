import { expect, type Locator, type Page } from '@playwright/test';

export const CHAT = { id: 'c1', title: 'Riyadh weather', revision: 0 };

export const CHANGE_COMMAND = {
  request_id: 'change:1',
  kind: 'executed',
  action: 'MemoryChange',
  settings: null,
  navigation: null,
  theme: null,
  changed: true,
  spoken: 'Memory saved.',
  detail: null,
  change: { id: 'chg-1', kind: 'memory_saved', label: 'Saved to memory', undoable: true, memory_id: 'm1' },
};

export async function mockApi(page: Page, turns: Record<string, unknown>[] = []) {
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) => route.fulfill({ json: [CHAT] }));
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: turns }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
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
}

export async function installRuntime(page: Page, denyMic = false, preferences: { theme?: 'light' | 'dark' } = {}) {
  await page.addInitScript(
    ({ deny, theme }) => {
      localStorage.setItem('sarjy-language', 'en');
      localStorage.setItem('sarjy-theme', theme);
      if (deny) {
        Object.defineProperty(navigator, 'mediaDevices', {
          configurable: true,
          value: {
            getUserMedia: async () => {
              throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
            },
          },
        });
      } else {
        const stream = { getTracks: () => [] } as unknown as MediaStream;
        Object.defineProperty(navigator, 'mediaDevices', {
          configurable: true,
          value: { getUserMedia: async () => stream },
        });
      }

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
      const runtime = new FakeRuntime();
      (window as unknown as { __layoutRuntime: unknown }).__layoutRuntime = runtime;
      window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => runtime;
    },
    { deny: denyMic, theme: preferences.theme ?? 'light' },
  );
}

export async function boot(page: Page, turns: Record<string, unknown>[] = []) {
  await mockApi(page, turns);
  await page.goto('/');
  // The first test can race a cold Vite compile; hydration needs longer than
  // the 15s default expectation.
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static', { timeout: 30_000 });
}

export async function startLive(page: Page) {
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
}

export async function showChangeToast(page: Page) {
  await page.evaluate(
    (command) =>
      (window as unknown as { __layoutRuntime: { simulate: (event: unknown) => void } }).__layoutRuntime.simulate({
        type: 'command',
        command,
      }),
    CHANGE_COMMAND,
  );
  const toast = page.locator('.change-toast');
  await expect(toast).toBeVisible();
  // The entrance animation starts 6px lower; measure the settled toast.
  await toast.evaluate((el) => Promise.all(el.getAnimations().map((animation) => animation.finished.catch(() => {}))));
}

/**
 * Bounding-box intersection. `margin` inflates the second box on every side,
 * so a check can require a real gap rather than merely zero overlap.
 */
export function intersects(a: { x: number; y: number; width: number; height: number }, b: typeof a, margin = 0) {
  return (
    a.x < b.x + b.width + margin &&
    b.x - margin < a.x + a.width &&
    a.y < b.y + b.height + margin &&
    b.y - margin < a.y + a.height
  );
}

export async function boxOf(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box, `expected ${await locator.evaluate((el) => el.outerHTML.slice(0, 80))} to have a box`).not.toBeNull();
  return box!;
}

export async function expectHit(locator: Locator, label: string) {
  const box = await boxOf(locator);
  const hit = await locator.evaluate(
    (el, point) => {
      const node = document.elementFromPoint(point.x, point.y);
      return node !== null && (node === el || el.contains(node));
    },
    { x: box.x + box.width / 2, y: box.y + box.height / 2 },
  );
  expect(hit, `${label} must be the hit target at its centre`).toBe(true);
}
