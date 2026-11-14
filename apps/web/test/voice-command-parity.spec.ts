import { expect, test, type Browser, type Page } from '@playwright/test';

declare global {
  interface Window {
    __emit?: (event: Record<string, unknown>) => void;
    __SARJY_VOICE_RUNTIME_FACTORY__?: () => unknown;
  }
}

const CHAT = { id: 'c1', title: 'Riyadh weather', revision: 0 };

const INITIAL_SETTINGS = {
  speaking_speed: 1,
  captions_visible: true,
  approved_voice_id: null,
  response_preference: 'automatic',
  revision: 0,
};

async function mockBackend(page: Page) {
  const actions: string[] = [];
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) => route.fulfill({ json: [CHAT] }));
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/settings', (route) => route.fulfill({ json: INITIAL_SETTINGS }));
  // Captions are a client effect in this slice. Any settings/action write is a
  // regression: record it and fail the request.
  await page.route('**/api/v1/actions', async (route) => {
    actions.push(route.request().url());
    await route.fulfill({ status: 500, json: { detail: { code: 'UNEXPECTED_SETTINGS_WRITE' } } });
  });
  return { actions };
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

async function boot(page: Page) {
  const backend = await mockBackend(page);
  await installFakeRuntime(page);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
  const settingsLoaded = page.waitForResponse('**/api/v1/settings');
  await page.goto('/');
  await settingsLoaded;
  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', '');
  // Wait for hydration and the bootstrap that follows runtime subscription.
  await expect(page.getByRole('button', { name: 'Riyadh weather', exact: true })).toBeVisible();
  await expect(page.locator('#shell-left')).toHaveAttribute('data-open', 'true');
  return backend;
}

async function emit(page: Page, event: Record<string, unknown>) {
  await page.evaluate((payload) => {
    (window as unknown as { __emit: (event: Record<string, unknown>) => void }).__emit(payload);
  }, event);
}

function command(overrides: Record<string, unknown> = {}) {
  return {
    request_id: 'command:turn-1',
    kind: 'executed',
    action: 'SetCaptionsVisible',
    settings: null,
    captions_visible: false,
    navigation: null,
    changed: true,
    spoken: 'Captions hidden.',
    detail: null,
    ...overrides,
  };
}

async function snapshot(page: Page) {
  const trace = await page.locator('[data-command-trace]').getAttribute('data-command-trace');
  const revision = await page.locator('[data-settings-revision]').getAttribute('data-settings-revision');
  const captions = await page.evaluate(() => localStorage.getItem('sarjy-captions'));
  return { trace, revision, captions };
}

/** A fresh browser context so persisted state never leaks between fixtures. */
async function withPage(browser: Browser, run: (page: Page, backend: { actions: string[] }) => Promise<void>) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  try {
    const backend = await boot(page);
    await run(page, backend);
  } finally {
    await context.close();
  }
}

test('GUI captions action and a voice result converge on one persisted state without a server write', async ({
  browser,
}) => {
  let guiState: Awaited<ReturnType<typeof snapshot>> | undefined;
  let voiceState: Awaited<ReturnType<typeof snapshot>> | undefined;

  await withPage(browser, async (page, backend) => {
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await page.getByRole('checkbox', { name: 'Show captions' }).uncheck();
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    guiState = await snapshot(page);
    expect(backend.actions).toHaveLength(0);
    await expect(page.locator('.captions')).toHaveCount(0);
  });

  await withPage(browser, async (page, backend) => {
    await emit(page, { type: 'command', command: command() });
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    voiceState = await snapshot(page);
    expect(backend.actions).toHaveLength(0);
    await expect(page.locator('.captions')).toHaveCount(0);
  });

  expect(voiceState).toEqual(guiState);
  expect(voiceState).toMatchObject({
    trace: 'SetCaptionsVisible',
    revision: '0',
    captions: 'off',
  });
});

test('a partial transcript dispatches nothing and a final transcript is not parsed in the browser', async ({
  page,
}) => {
  await boot(page);

  await emit(page, { type: 'transcript', text: 'Hide captions', final: false, segmentId: 'seg-1', speaker: 'user' });
  await emit(page, { type: 'transcript', text: 'Hide captions.', final: true, segmentId: 'seg-1', speaker: 'user' });

  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', '');
  await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', '');
  expect(await snapshot(page)).toMatchObject({ captions: 'on' });
});

test('a duplicate finalized command result applies once', async ({ page }) => {
  await boot(page);
  await emit(page, { type: 'command', command: command() });
  await emit(page, { type: 'command', command: command() });
  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', 'SetCaptionsVisible');
  expect(await snapshot(page)).toMatchObject({ revision: '0', captions: 'off' });
});

test('voice navigation opens the same panel the GUI control opens', async ({ browser }) => {
  const guiOpen: string[] = [];
  const voiceOpen: string[] = [];

  await withPage(browser, async (page) => {
    await page.getByRole('button', { name: 'Show conversations', exact: true }).click();
    await expect(page.locator('#shell-left')).toHaveAttribute('data-open', 'false');
    await page.getByRole('button', { name: 'Show conversations', exact: true }).click();
    await expect(page.locator('#shell-left')).toHaveAttribute('data-open', 'true');
    guiOpen.push((await page.locator('.sidebar-left').getAttribute('data-open')) ?? '');
  });

  await withPage(browser, async (page) => {
    await page.getByRole('button', { name: 'Show conversations', exact: true }).click();
    await expect(page.locator('#shell-left')).toHaveAttribute('data-open', 'false');
    await emit(page, {
      type: 'command',
      command: command({
        action: 'ShowConversationHistory',
        captions_visible: null,
        navigation: 'history',
        spoken: 'Opening your previous conversations.',
      }),
    });
    await expect(page.locator('#shell-left')).toHaveAttribute('data-open', 'true');
    voiceOpen.push((await page.locator('.sidebar-left').getAttribute('data-open')) ?? '');
  });

  expect(voiceOpen).toEqual(guiOpen);
});
