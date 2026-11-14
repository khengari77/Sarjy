import { expect, test, type Page } from '@playwright/test';

const CHAT = { id: 'c1', title: 'Voice switch', revision: 0 };
const VOICES = [
  { id: 'voice-a', name: 'Voice A', languages: ['en'] },
  { id: 'voice-b', name: 'Voice B', languages: ['en'] },
];

type Mode = 'applied' | 'failed';

async function mockApp(page: Page, mode: Mode) {
  const actionBodies: Record<string, unknown>[] = [];
  const sessionPosts: string[] = [];
  const sessionEnds: string[] = [];
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: [CHAT] })
      : route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } }),
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/voices?*', (route) => route.fulfill({ json: { voices: VOICES } }));
  await page.route('**/api/v1/voice/sessions', (route) => {
    sessionPosts.push(route.request().url());
    return route.fulfill({
      json: {
        id: 'sess-1',
        url: 'ws://fake.invalid',
        token: 'fake',
        expires_at: new Date(Date.now() + 600_000).toISOString(),
      },
    });
  });
  await page.route('**/api/v1/voice/sessions/*/end', (route) => {
    sessionEnds.push(route.request().url());
    return route.fulfill({ json: {} });
  });
  await page.route('**/api/v1/settings', (route) =>
    route.fulfill({
      json: {
        speaking_speed: 1,
        captions_visible: true,
        approved_voice_id: 'voice-a',
        response_preference: 'automatic',
        revision: 1,
      },
    }),
  );
  await page.route('**/api/v1/actions', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    actionBodies.push(body);
    const voiceId = (body.action as { voice_id: string }).voice_id;
    await route.fulfill({
      json: {
        handled: true,
        result: {
          kind: 'SelectApprovedVoice',
          changed: true,
          settings: {
            speaking_speed: 1,
            captions_visible: true,
            approved_voice_id: voiceId,
            response_preference: 'automatic',
            revision: 2,
          },
          voice_control: {
            session_id: 'sess-1',
            conversation_id: 'c1',
            active_voice_id: 'voice-a',
            active_settings_revision: 1,
            request_id: body.request_id,
            voice_id: voiceId,
            settings_revision: 2,
            status: 'dispatched',
            error_code: null,
          },
        },
      },
    });
  });
  await page.route('**/api/v1/voice/sessions/*/voice', (route) => {
    const requestId = actionBodies.at(-1)?.request_id;
    const voiceId = (actionBodies.at(-1)?.action as { voice_id: string } | undefined)?.voice_id;
    route.fulfill({
      json: {
        session_id: 'sess-1',
        conversation_id: 'c1',
        active_voice_id: mode === 'applied' ? voiceId : 'voice-a',
        active_settings_revision: mode === 'applied' ? 2 : 1,
        request_id: requestId,
        voice_id: voiceId,
        settings_revision: 2,
        status: mode,
        error_code: mode === 'failed' ? 'VOICE_SWITCH_FAILED' : null,
      },
    });
  });
  return { actionBodies, sessionPosts, sessionEnds };
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
    const handlers = new Set<(event: unknown) => void>();
    const runtime = {
      __epoch: 0,
      get epoch() {
        return this.__epoch;
      },
      subscribe(handler: (event: unknown) => void) {
        handlers.add(handler);
        return () => handlers.delete(handler);
      },
      emit(event: Record<string, unknown>) {
        for (const handler of [...handlers]) handler({ ...event, epoch: this.__epoch });
      },
      async connect() {
        this.__epoch += 1;
        this.emit({ type: 'status', source: 'transport', text: 'connected' });
        this.emit({ type: 'status', source: 'agent', text: 'listening' });
      },
      async disconnect() {
        this.__epoch += 1;
      },
      async cancelConnect() {
        this.__epoch += 1;
      },
      async startMicrophone() {},
      async stopMicrophone() {},
      async sendText() {},
      async resumeAudio() {},
      async interrupt() {},
      microphoneStream() {
        return null;
      },
      remoteAudioLevel() {
        return 0;
      },
    };
    (window as unknown as { __SARJY_VOICE_RUNTIME_FACTORY__: () => unknown }).__SARJY_VOICE_RUNTIME_FACTORY__ = () =>
      runtime;
  });
}

async function startCall(page: Page) {
  await page.goto('/');
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
  await page.getByRole('textbox', { name: 'Search voices' }).fill('');
  await page.getByRole('button', { name: 'Load voices / Search', exact: true }).click();
  await page.getByLabel('Choose voice').selectOption('voice-a');
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(page.getByText('Active voice: Voice A')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sarjy-language', 'en');
    localStorage.removeItem('sarjy-voice-id');
  });
  await allowMicrophone(page);
  await installFakeRuntime(page);
});

test('shows Updating voice… until the worker ACK confirms the new active voice', async ({ page }) => {
  const { actionBodies, sessionPosts, sessionEnds } = await mockApp(page, 'applied');
  await startCall(page);
  await page.getByLabel('Choose voice').selectOption('voice-b');
  await expect(page.getByText('Updating voice…')).toBeVisible();
  await expect(page.getByText('Active voice: Voice B')).toBeVisible();
  await expect(page.getByText('Updating voice…')).toHaveCount(0);
  expect(actionBodies).toHaveLength(1);
  expect(actionBodies[0].action).toEqual({ kind: 'SelectApprovedVoice', voice_id: 'voice-b' });
  expect(actionBodies[0].request_id).toBeTruthy();
  expect(actionBodies[0].expected_revision).toBe(1);
  // No reconnect and no extra paid session: the same room/session keeps running.
  expect(sessionPosts).toHaveLength(1);
  expect(sessionEnds).toHaveLength(0);
});

test('a failed worker switch keeps the old voice active and reverts the selection', async ({ page }) => {
  await mockApp(page, 'failed');
  await startCall(page);
  await page.getByLabel('Choose voice').selectOption('voice-b');
  await expect(page.getByRole('alert')).toContainText('previous voice is still active');
  await expect(page.getByText('Active voice: Voice A')).toBeVisible();
  await expect(page.getByLabel('Choose voice')).toHaveValue('voice-a');
  await expect(page.getByText('Updating voice…')).toHaveCount(0);
});
