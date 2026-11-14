import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
  await page.addInitScript(() => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
  });
});

test('orb is a single keyboard-operable control that starts one session', async ({ page }) => {
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionPosts += 1;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });

  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toBeVisible();
  await expect(orb).toHaveAttribute('type', 'button');
  await expect(orb).toHaveAttribute('data-phase', 'idle');
  await expect(orb).toBeEnabled();
  // The component renders statically on the server; wait for hydration before
  // sending keyboard input so the click handler is attached.
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');

  // Bootstrap briefly disables the control; retry until focus sticks.
  await expect(async () => {
    await orb.focus();
    await expect(orb).toBeFocused({ timeout: 500 });
  }).toPass({ timeout: 10000 });
  await page.keyboard.press('Enter');

  await expect(page.locator('[data-code="PIPELINE_NOT_CONFIGURED"]')).toContainText('Voice is not configured');
  expect(sessionPosts).toBe(1);
});

test('orb honours reduced motion with a static renderer', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toBeVisible();
  await expect(orb).toHaveAttribute('data-renderer', 'static');
  await expect(orb.locator('canvas')).toHaveCount(0);
  await expect(orb.locator('.orb-static')).toHaveCount(1);
});

test('orb uses the enhanced renderer on capable browsers', async ({ page }) => {
  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toBeVisible();
  // Canvas2D is also "not static", so the claim in the title needs the
  // enhanced tier itself.
  await expect(orb).toHaveAttribute('data-renderer', 'enhanced');
});

test('particle field rotates over time while idle', async ({ page }) => {
  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');
  await page.waitForTimeout(900);

  const first = await orb.screenshot();
  await page.waitForTimeout(1200);
  const second = await orb.screenshot();

  expect(first.equals(second)).toBe(false);
});

test('reduced-motion orb is visually stable across frames', async ({ page }) => {
  // The demo-session boot request is slow on a cold SSR compile; keep the wait
  // (and the test's budget) explicitly above the 30 s default, together.
  test.setTimeout(90_000);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  // The static markup is server-rendered, so `data-renderer="static"` is true
  // before hydration. Wait for hydration's bootstrap request to finish before
  // sampling: while it runs the button is disabled and dimmed to 70%.
  const boot = page.waitForResponse(
    (response) => response.url().includes('/api/v1/demo-session') && response.request().method() === 'POST',
    { timeout: 60000 },
  );
  await page.goto('/');
  await boot;
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toHaveAttribute('data-renderer', 'static');
  await expect(orb).toBeEnabled();
  await expect(orb.locator('canvas')).toHaveCount(0);

  // The static tier must not animate: no CSS animation and no transition may
  // still be running once the shell has settled.
  const inert = await orb.evaluate((element) => {
    const nodes = [element, ...element.querySelectorAll('*')];
    return nodes.every((node) => {
      const style = getComputedStyle(node);
      const durations = style.transitionDuration.split(',').map((value) => Number.parseFloat(value));
      return style.animationName === 'none' && durations.every((duration) => duration === 0);
    });
  });
  expect(inert, 'static tier must not run CSS animations or transitions').toBe(true);

  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });

  const first = await orb.screenshot({ animations: 'disabled' });
  await page.waitForTimeout(900);
  await expect(orb).toHaveAttribute('data-renderer', 'static');
  await expect(orb.locator('canvas')).toHaveCount(0);
  const second = await orb.screenshot({ animations: 'disabled' });

  expect(first.equals(second)).toBe(true);
});

test('two activations in the same tick create only one session', async ({ page }) => {
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionPosts += 1;
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });

  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');
  await expect(async () => {
    await orb.focus();
    await expect(orb).toBeFocused({ timeout: 500 });
  }).toPass({ timeout: 10000 });

  await page.evaluate(() => {
    const button = document.querySelector<HTMLButtonElement>('.orb-button');
    button?.click();
    button?.click();
  });

  await expect(page.locator('[data-code="PIPELINE_NOT_CONFIGURED"]')).toContainText('Voice is not configured');
  expect(sessionPosts).toBe(1);
});

test('reduced-motion orb shows a state glyph and label, not just colour', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toHaveAttribute('data-renderer', 'static');
  await expect(orb.locator('.orb-glyph')).toHaveText('●');
  await expect(orb.locator('.orb-state-text')).toHaveText('Ready');
});

test('a lost WebGL context falls back to Canvas2D without breaking the session', async ({ page }) => {
  await page.addInitScript(() => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
    class FakeRuntime {
      __epoch = 1;
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
        this.emit({ type: 'status', source: 'transport', text: 'connected' });
        this.emit({ type: 'status', source: 'agent', text: 'listening' });
      }
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
    window.__SARJY_VOICE_RUNTIME_FACTORY__ = () => new FakeRuntime();
  });
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
  await page.route('**/api/v1/conversations', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: [] })
      : route.fulfill({ json: { id: 'c-live', title: 'Live', revision: 0 } }),
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));

  await page.goto('/');
  await expect(page.getByText('No conversations yet.')).toBeVisible();
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  const orb = page.locator('.orb-button');
  // This scenario only exists on the WebGL tier. When the probe or the first
  // context creation fell back to Canvas2D there is no context to lose, so
  // skip rather than assert a weaker "not static" claim.
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');
  const tier = await orb.getAttribute('data-renderer');
  test.skip(tier !== 'enhanced', `WebGL2 is unavailable; the orb is on the ${tier} tier`);

  const lost = await page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement>('.orb-button canvas');
    const gl = canvas?.getContext('webgl2');
    if (!gl) return false;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  });
  test.skip(!lost, 'the orb canvas has no WebGL2 context to lose');

  // A canvas that has owned a WebGL2 context cannot hand out 2d, so the only
  // correct fallback is the fresh element the tier remount builds: the orb
  // must keep drawing, and the call must stay alive.
  await expect(orb).toHaveAttribute('data-renderer', 'light');
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  await expect(orb.locator('canvas.orb-canvas')).toHaveCount(1);
  await expect(orb.locator('.orb-static-state')).toHaveCount(0);
});
