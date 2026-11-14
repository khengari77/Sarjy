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
const VOICE_SETTINGS = { ...INITIAL_SETTINGS, approved_voice_id: 'approved', revision: 1 };

interface Backend {
  settingsWrites: { request_id: string; action: Record<string, unknown> }[];
  actionWrites: string[];
}

async function mockBackend(page: Page): Promise<Backend> {
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
  const captions = await page.locator('[data-captions-visible]').getAttribute('data-captions-visible');
  const voice = await page.locator('[data-approved-voice]').getAttribute('data-approved-voice');
  return { trace, revision, captions, voice };
}

async function withPage(browser: Browser, run: (page: Page, backend: Backend) => Promise<void>) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  try {
    const backend = await boot(page);
    await run(page, backend);
  } finally {
    await context.close();
  }
}

test('GUI captions action and a voice result converge through the one settings path', async ({ browser }) => {
  let guiState: Awaited<ReturnType<typeof snapshot>> | undefined;
  let voiceState: Awaited<ReturnType<typeof snapshot>> | undefined;

  await withPage(browser, async (page, backend) => {
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await page.getByRole('checkbox', { name: 'Show captions' }).uncheck();
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    guiState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(1);
    expect(backend.settingsWrites[0]).toMatchObject({
      request_id: 'captions:0:off',
      action: { kind: 'SetCaptionsVisible', visible: false },
    });
    expect(backend.actionWrites).toHaveLength(0);
    await expect(page.locator('.captions')).toHaveCount(0);
  });

  await withPage(browser, async (page, backend) => {
    await emit(page, { type: 'command', command: command() });
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    voiceState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(0);
    expect(backend.actionWrites).toHaveLength(0);
    await expect(page.locator('.captions')).toHaveCount(0);
  });

  expect(voiceState).toEqual(guiState);
  expect(voiceState).toMatchObject({ trace: 'SetCaptionsVisible', revision: '1', captions: 'false' });
});

test('GUI picker and voice selection converge on the same approved-voice state', async ({ browser }) => {
  let guiState: Awaited<ReturnType<typeof snapshot>> | undefined;
  let voiceState: Awaited<ReturnType<typeof snapshot>> | undefined;

  await withPage(browser, async (page, backend) => {
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await page.getByRole('button', { name: 'Load voices / Search', exact: true }).click();
    await page.getByLabel('Choose voice').selectOption('approved');
    await expect(page.locator('[data-settings-revision]')).toHaveAttribute('data-settings-revision', '1');
    guiState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(1);
    expect(backend.settingsWrites[0]).toMatchObject({
      request_id: 'voice:0:approved',
      action: { kind: 'SelectApprovedVoice', voice_id: 'approved' },
    });
    expect(backend.actionWrites).toHaveLength(0);
    await expect(page.getByLabel('Choose voice')).toHaveValue('approved');
  });

  await withPage(browser, async (page, backend) => {
    await emit(page, {
      type: 'command',
      command: command({
        action: 'SelectApprovedVoice',
        settings: VOICE_SETTINGS,
        spoken: 'Voice set to Approved Narrator for the next call.',
      }),
    });
    await expect(page.locator('[data-settings-revision]')).toHaveAttribute('data-settings-revision', '1');
    voiceState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(0);
    expect(backend.actionWrites).toHaveLength(0);
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await expect(page.getByLabel('Choose voice')).toHaveValue('approved');
  });

  expect(voiceState).toEqual(guiState);
  expect(voiceState).toMatchObject({ trace: 'SelectApprovedVoice', revision: '1', voice: 'approved' });
});

test('a partial transcript dispatches nothing and a final transcript is not parsed in the browser', async ({
  page,
}) => {
  await boot(page);

  await emit(page, { type: 'transcript', text: 'Hide captions', final: false, segmentId: 'seg-1', speaker: 'user' });
  await emit(page, { type: 'transcript', text: 'Hide captions.', final: true, segmentId: 'seg-1', speaker: 'user' });

  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', '');
  await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', '');
  expect(await snapshot(page)).toMatchObject({ captions: 'true' });
});

test('a duplicate finalized command result applies once', async ({ page }) => {
  await boot(page);
  await emit(page, { type: 'command', command: command() });
  await emit(page, { type: 'command', command: command() });
  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', 'SetCaptionsVisible');
  expect(await snapshot(page)).toMatchObject({ revision: '1', captions: 'false' });
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
