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

const HIDDEN_SETTINGS = { ...INITIAL_SETTINGS, captions_visible: false, revision: 1 };
const SLOWER_SETTINGS = { ...INITIAL_SETTINGS, speaking_speed: 0.9, revision: 1 };

async function mockBackend(page: Page) {
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [CHAT] }) : route.fulfill({ json: CHAT }),
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/settings', (route) => route.fulfill({ json: INITIAL_SETTINGS }));
  await page.route('**/api/v1/actions', async (route) => {
    const body = route.request().postDataJSON() as { action: Record<string, unknown> };
    const next =
      body.action.kind === 'SetCaptionsVisible'
        ? { ...HIDDEN_SETTINGS, captions_visible: body.action.visible === true }
        : { ...SLOWER_SETTINGS };
    await route.fulfill({
      json: { handled: true, result: { kind: body.action.kind, settings: next, changed: true } },
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
  await mockBackend(page);
  await installFakeRuntime(page);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
  await page.goto('/');
  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', '');
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
    settings: HIDDEN_SETTINGS,
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
  const speed = await page.evaluate(() => localStorage.getItem('sarjy-speaking-speed'));
  return { trace, revision, captions, speed };
}

/** A fresh browser context so persisted state never leaks between fixtures. */
async function withPage(browser: Browser, run: (page: Page) => Promise<void>) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  try {
    await boot(page);
    await run(page);
  } finally {
    await context.close();
  }
}

test('GUI captions action and a voice result converge on one persisted state', async ({ browser }) => {
  let guiState: Awaited<ReturnType<typeof snapshot>> | undefined;
  let voiceState: Awaited<ReturnType<typeof snapshot>> | undefined;

  await withPage(browser, async (page) => {
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await page.getByRole('checkbox', { name: 'Show captions' }).uncheck();
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    guiState = await snapshot(page);
    await expect(page.locator('.captions')).toHaveCount(0);
  });

  await withPage(browser, async (page) => {
    await emit(page, { type: 'command', command: command() });
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    voiceState = await snapshot(page);
    await expect(page.locator('.captions')).toHaveCount(0);
  });

  expect(voiceState).toEqual(guiState);
  expect(voiceState).toMatchObject({
    trace: 'SetCaptionsVisible',
    revision: '1',
    captions: 'off',
  });
});

test('GUI speed slider and a voice result converge on one persisted state', async ({ browser }) => {
  let guiState: Awaited<ReturnType<typeof snapshot>> | undefined;
  let voiceState: Awaited<ReturnType<typeof snapshot>> | undefined;

  await withPage(browser, async (page) => {
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await page.locator('#speaking-speed').evaluate((element) => {
      const input = element as HTMLInputElement;
      input.value = '0.9';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    guiState = await snapshot(page);
    await expect(page.locator('#speaking-speed')).toHaveValue('0.9');
  });

  await withPage(browser, async (page) => {
    await emit(page, {
      type: 'command',
      command: command({
        action: 'AdjustSpeakingSpeed',
        settings: SLOWER_SETTINGS,
        spoken: 'Speaking slower.',
      }),
    });
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    voiceState = await snapshot(page);
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await expect(page.locator('#speaking-speed')).toHaveValue('0.9');
  });

  expect(voiceState).toEqual(guiState);
  expect(voiceState).toMatchObject({ trace: 'AdjustSpeakingSpeed', revision: '1', speed: '0.9' });
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
  expect(await snapshot(page)).toMatchObject({ revision: '1', captions: 'off' });
});

test('voice navigation opens the same panel the GUI control opens', async ({ browser }) => {
  const guiOpen: string[] = [];
  const voiceOpen: string[] = [];

  await withPage(browser, async (page) => {
    // The desktop left dock is open at this viewport; close it first.
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
        settings: null,
        navigation: 'history',
        spoken: 'Opening your previous conversations.',
      }),
    });
    await expect(page.locator('#shell-left')).toHaveAttribute('data-open', 'true');
    voiceOpen.push((await page.locator('.sidebar-left').getAttribute('data-open')) ?? '');
  });

  expect(voiceOpen).toEqual(guiOpen);
});
