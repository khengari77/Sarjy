import { expect, test, type Page } from '@playwright/test';

// Cross-component regressions for the shell's modal drawers under LIVE-CALL
// state. The shell-only spec cannot see these: the inert background covers the
// stage, where the end-call confirmation and the call controls live.

const CHATS = [
  { id: 'c1', title: 'Trip to Paris', revision: 0 },
  { id: 'c2', title: 'Work ideas', revision: 0 },
];

async function mockApi(page: Page) {
  let created = 0;
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: CHATS });
    created += 1;
    return route.fulfill({ json: { id: 'c-new', title: 'New conversation', revision: 0 } });
  });
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
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
  const ends: string[] = [];
  await page.route('**/api/v1/voice/sessions/*/end', (route) => {
    ends.push(route.request().url());
    return route.fulfill({ json: {} });
  });
  return { created: () => created, ends };
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

async function waitForShell(page: Page) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const hydrated = await page
      .waitForFunction(
        () =>
          Boolean(document.querySelector('.orb-button')) &&
          document.querySelector('.orb-button')?.getAttribute('data-renderer') !== 'static',
        null,
        { timeout: 12_000 },
      )
      .then(() => true)
      .catch(() => false);
    if (hydrated) return;
    // This shared machine drops in-flight module requests on network changes
    // (net::ERR_NETWORK_CHANGED). That leaves SSR markup unhydrated or shows
    // SvelteKit's 500 page; a reload re-runs hydration.
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  throw new Error('shell never hydrated');
}

async function startCall(page: Page) {
  await page.goto('/');
  await waitForShell(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
}

async function openLeftDrawer(page: Page) {
  const trigger = page.getByRole('button', { name: 'Show conversations' });
  await trigger.click();
  await expect(page.locator('#shell-left')).toBeVisible();
  return trigger;
}

function isInert(page: Page, selector: string): Promise<boolean> {
  return page.locator(selector).evaluate((el) => (el as HTMLElement).inert);
}

/** The shell's modal layer: the confirmation is portalled into this region. */
function dialogRegion(page: Page) {
  return page.locator('.stage-dialog');
}

function confirmDialog(page: Page) {
  return page.getByRole('dialog', { name: 'End the current voice session?' });
}

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await allowMicrophone(page);
  await installFakeRuntime(page);
});

test.describe('mobile overlay drawer during a live call', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('switching conversations hands the modal layer to the confirmation, which is focusable and usable', async ({
    page,
  }) => {
    const api = await mockApi(page);
    await startCall(page);
    const trigger = await openLeftDrawer(page);

    await page.locator('#shell-left').getByRole('button', { name: 'Work ideas', exact: true }).click();
    const dialog = confirmDialog(page);
    await expect(dialog).toBeVisible();

    // The drawer handed off. The confirmation is the modal layer: the stage
    // body, header and control strip are all inert, and the dialog itself is
    // the only interactive region - so aria-modal="true" is true, not a claim.
    await expect(page.locator('#shell-left')).toBeHidden();
    expect(await dialog.evaluate((el) => el.closest('[inert]') === null)).toBe(true);
    expect(await isInert(page, '.stage-dialog')).toBe(false);
    expect(await isInert(page, '.stage-content')).toBe(true);
    expect(await isInert(page, '.topbar')).toBe(true);
    expect(await isInert(page, '.stage-controls')).toBe(true);
    await expect(page.locator('.stage-content')).toHaveAttribute('inert', '');
    await expect(page.locator('.topbar')).toHaveAttribute('inert', '');
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    expect(api.ends).toHaveLength(0);

    // A Tab walk from Cancel never leaves the dialog region (no native trap on
    // a div role=dialog, so this only holds if the shell wraps it).
    for (let index = 0; index < 10; index += 1) {
      await page.keyboard.press('Tab');
      expect(
        await dialogRegion(page).evaluate((el) => el.contains(document.activeElement)),
        `dialog tab #${index + 1} escaped the modal layer`,
      ).toBe(true);
    }

    // Escape dismisses the dialog from wherever the walk left focus.
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    // Inert is cleared again, focus returns to the drawer trigger that opened
    // the flow, and the previously-inert control strip works.
    expect(await isInert(page, '.stage-content')).toBe(false);
    expect(await isInert(page, '.topbar')).toBe(false);
    expect(await isInert(page, '.stage-controls')).toBe(false);
    await expect(trigger).toBeFocused();
    const mute = page.getByRole('button', { name: 'Mute', exact: true });
    await mute.focus();
    await expect(mute).toBeFocused();
    await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

    // Same flow through the confirm path.
    await openLeftDrawer(page);
    await page.locator('#shell-left').getByRole('button', { name: 'Work ideas', exact: true }).click();
    await dialog.getByRole('button', { name: 'End & switch' }).click();
    await expect.poll(() => api.ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
    await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
    await expect(page.locator('#shell-left')).toBeHidden();
  });

  test('creating a conversation from the drawer keeps the confirmation reachable', async ({ page }) => {
    const api = await mockApi(page);
    await startCall(page);
    await openLeftDrawer(page);

    await page.locator('#shell-left').getByRole('button', { name: 'New conversation +' }).click();
    const dialog = confirmDialog(page);
    await expect(dialog).toBeVisible();
    expect(await dialog.evaluate((el) => el.closest('[inert]') === null)).toBe(true);
    expect(await isInert(page, '.stage-content')).toBe(true);
    expect(await isInert(page, '.topbar')).toBe(true);
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();

    await dialog.getByRole('button', { name: 'End & start' }).click();
    await expect.poll(() => api.created()).toBe(1);
    await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  });

  test('Mute/End stay reachable and operable while the drawer is modal', async ({ page }) => {
    // Tablet keeps the control strip visible beside the drawer; phones stack the
    // drawer over it, where keyboard/AT reach still matters.
    await page.setViewportSize({ width: 900, height: 720 });
    await mockApi(page);
    await startCall(page);
    await openLeftDrawer(page);

    // The control strip is outside the inert set (#37 explicit requirement).
    expect(await page.locator('.stage-controls').evaluate((el) => (el as HTMLElement).inert)).toBe(false);
    expect(await page.locator('.stage-controls').evaluate((el) => el.closest('[inert]') === null)).toBe(true);

    const mute = page.getByRole('button', { name: 'Mute', exact: true });
    await mute.focus();
    await expect(mute).toBeFocused();
    await mute.click();
    await expect(page.getByRole('button', { name: 'Enable microphone', exact: true })).toBeVisible();

    const end = page.getByRole('button', { name: 'End call', exact: true });
    await end.focus();
    await expect(end).toBeFocused();

    // Keyboard reaches the strip too: Tab cycles drawer + control surface.
    await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).focus();
    let enteredControls = false;
    for (let index = 0; index < 15; index += 1) {
      await page.keyboard.press('Tab');
      if (await page.locator('.stage-controls').evaluate((el) => el.contains(document.activeElement))) {
        enteredControls = true;
        break;
      }
    }
    expect(enteredControls).toBe(true);
  });
});

test('Escape closes the open dock that holds focus, not the most recently opened one', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  const left = page.getByRole('button', { name: 'Show conversations' });
  const right = page.getByRole('button', { name: 'Show memory and settings' });
  await expect(left).toHaveAttribute('aria-expanded', 'true');

  // Open the right dock last, then move focus into the left dock.
  await right.click();
  await expect(right).toHaveAttribute('aria-expanded', 'true');
  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).focus();
  await expect(page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true })).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(left).toHaveAttribute('aria-expanded', 'false');
  await expect(right).toHaveAttribute('aria-expanded', 'true');
  await expect(left).toBeFocused();

  // Mirror: reopen the left dock, focus the right dock, Escape closes right.
  await left.click();
  await expect(left).toHaveAttribute('aria-expanded', 'true');
  await page.locator('#shell-right').getByRole('tab', { name: 'Voice' }).focus();
  await page.keyboard.press('Escape');
  await expect(right).toHaveAttribute('aria-expanded', 'false');
  await expect(left).toHaveAttribute('aria-expanded', 'true');
  await expect(right).toBeFocused();
});
