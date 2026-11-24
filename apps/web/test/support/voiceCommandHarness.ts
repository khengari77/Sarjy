import { expect, type Browser, type Page } from '@playwright/test';

declare global {
  interface Window {
    __emit?: (event: Record<string, unknown>) => void;
    __SARJY_VOICE_RUNTIME_FACTORY__?: () => unknown;
  }
}

const CHAT = { id: 'c1', title: 'Riyadh weather', revision: 0 };

export const INITIAL_SETTINGS = {
  speaking_speed: 1,
  captions_visible: true,
  approved_voice_id: null,
  response_preference: 'automatic',
  revision: 0,
};

export const HIDDEN_SETTINGS = { ...INITIAL_SETTINGS, captions_visible: false, revision: 1 };
export const VOICE_SETTINGS = { ...INITIAL_SETTINGS, approved_voice_id: 'approved', revision: 1 };

export interface Backend {
  settingsWrites: { request_id: string; action: Record<string, unknown> }[];
  actionWrites: string[];
}

export async function mockBackend(page: Page): Promise<Backend> {
  const backend: Backend = { settingsWrites: [], actionWrites: [] };
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) => route.fulfill({ json: [CHAT] }));
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/settings', (route) => route.fulfill({ json: INITIAL_SETTINGS }));
  await page.route('**/api/v1/voices?*', (route) =>
    route.fulfill({
      json: {
        voices: [
          { id: 'approved', name: 'Approved Narrator', languages: ['en'] },
          { id: 'other', name: 'Other Voice', languages: ['en'] },
        ],
        next_page_token: null,
      },
    }),
  );
  await page.route('**/api/v1/settings/actions', async (route) => {
    const body = route.request().postDataJSON() as { request_id: string; action: Record<string, unknown> };
    backend.settingsWrites.push(body);
    const next =
      body.action.kind === 'SetCaptionsVisible'
        ? { ...INITIAL_SETTINGS, captions_visible: body.action.visible === true, revision: 1 }
        : VOICE_SETTINGS;
    await route.fulfill({ json: { kind: body.action.kind, settings: next, changed: true } });
  });
  // The conversation-scoped action endpoint must never receive a settings write.
  await page.route('**/api/v1/actions', async (route) => {
    backend.actionWrites.push(route.request().url());
    await route.fulfill({ status: 500, json: { detail: { code: 'UNEXPECTED_CONVERSATION_WRITE' } } });
  });
  return backend;
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
      async connect() {}
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
    (window as unknown as { __emit: (event: Record<string, unknown>) => void }).__emit = (event) => runtime.emit(event);
    window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => runtime;
  });
}

export async function boot(page: Page) {
  const backend = await mockBackend(page);
  await installFakeRuntime(page);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
  const settingsLoaded = page.waitForResponse('**/api/v1/settings');
  await page.goto('/');
  await settingsLoaded;
  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', '');
  await expect(page.getByRole('button', { name: 'Riyadh weather', exact: true })).toBeVisible();
  await expect(page.locator('#shell-left')).toHaveAttribute('data-open', 'true');
  return backend;
}

export async function emit(page: Page, event: Record<string, unknown>) {
  await page.evaluate((payload) => {
    (window as unknown as { __emit: (event: Record<string, unknown>) => void }).__emit(payload);
  }, event);
}

export function command(overrides: Record<string, unknown> = {}) {
  return {
    request_id: 'command:turn-1',
    kind: 'executed',
    action: 'SetCaptionsVisible',
    settings: HIDDEN_SETTINGS,
    navigation: null,
    theme: null,
    changed: true,
    spoken: 'Captions hidden.',
    detail: null,
    ...overrides,
  };
}

export async function snapshot(page: Page) {
  const trace = await page.locator('[data-command-trace]').getAttribute('data-command-trace');
  const revision = await page.locator('[data-settings-revision]').getAttribute('data-settings-revision');
  const captions = await page.locator('[data-captions-visible]').getAttribute('data-captions-visible');
  const voice = await page.locator('[data-approved-voice]').getAttribute('data-approved-voice');
  return { trace, revision, captions, voice };
}

export async function withPage(browser: Browser, run: (page: Page, backend: Backend) => Promise<void>) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  try {
    const backend = await boot(page);
    await run(page, backend);
  } finally {
    await context.close();
  }
}
