import { mkdir } from 'node:fs/promises';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { ready, mockConversations, mockSessions } from './support/raceHarness';

const EVIDENCE = 'test-results/orb-evidence';

interface MicState {
  calls: number;
  stops: number;
  level: number;
}

async function installFakeMicrophone(page: Page, level = 0.5) {
  await page.addInitScript(
    ({ level }) => {
      const state = { calls: 0, stops: 0, level, tracks: [] as { stop(): void }[] };
      const track = {
        stop() {
          state.stops += 1;
        },
      };
      state.tracks.push(track);
      const stream = {
        getTracks: () => state.tracks,
        getAudioTracks: () => state.tracks,
      };
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: {
          getUserMedia: async () => {
            state.calls += 1;
            return stream;
          },
        },
      });
      class FakeAnalyser {
        fftSize = 512;
        smoothingTimeConstant = 0.6;
        getFloatTimeDomainData(samples: Float32Array) {
          samples.fill(state.level);
        }
        connect() {}
        disconnect() {}
      }
      class FakeAudioContext {
        createMediaStreamSource() {
          return { connect() {}, disconnect() {} };
        }
        createAnalyser() {
          return new FakeAnalyser();
        }
        close() {
          return Promise.resolve();
        }
      }
      Object.defineProperty(window, 'AudioContext', { configurable: true, value: FakeAudioContext });
      Object.defineProperty(window, 'webkitAudioContext', { configurable: true, value: FakeAudioContext });
      (window as unknown as { __mic: MicState }).__mic = state as unknown as MicState;
      (window as unknown as { __micStream: MediaStream }).__micStream = stream as unknown as MediaStream;
    },
    { level },
  );
}

async function micState(page: Page): Promise<MicState> {
  return page.evaluate(() => (window as unknown as { __mic: MicState }).__mic);
}

async function setTheme(page: Page, theme: 'light' | 'dark') {
  await page.addInitScript((value) => localStorage.setItem('sarjy-theme', value), theme);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
}

async function shot(page: Page, name: string) {
  await mkdir(EVIDENCE, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: `${EVIDENCE}/${name}.png`, animations: 'disabled' });
}

test('no microphone is requested on page load or before the orb is activated', async ({ page }) => {
  await installFakeMicrophone(page);
  await page.goto('/');
  await ready(page);

  expect((await micState(page)).calls).toBe(0);
  await page.waitForTimeout(500);
  expect((await micState(page)).calls).toBe(0);
});

test('the orb reacts to the real microphone while connecting, then the preview stops', async ({ page }) => {
  await mockConversations(page);
  await installFakeMicrophone(page, 0.5);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route('**/api/v1/voice/sessions', async (route) => {
    await gate;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });

  await page.goto('/');
  await ready(page);
  const orb = page.locator('.orb-button');

  await orb.click();
  await expect(orb).toHaveAttribute('data-phase', 'connecting');
  await expect.poll(async () => (await micState(page)).calls).toBe(1);

  // A real (mock) microphone envelope reaches the orb's render state while the
  // session is still connecting, before `live` exists.
  await expect.poll(async () => Number(await orb.getAttribute('data-mic-level'))).toBeGreaterThan(0);
  await shot(page, 'connecting');

  release();
  await expect(page.locator('[data-code="PIPELINE_NOT_CONFIGURED"]')).toBeVisible();
  await expect.poll(async () => (await micState(page)).stops).toBeGreaterThan(0);
  await expect(orb).toHaveAttribute('data-mic-level', '0.00');
});

test('in-call mic and output levels reach the orb render state', async ({ page }) => {
  await mockConversations(page);
  await mockSessions(page);
  await installFakeMicrophone(page, 0.5);
  await page.addInitScript(() => {
    class FakeRuntime {
      __epoch = 0;
      publishedAt = performance.now() + 400;
      handlers = new Set<(event: Record<string, unknown>) => void>();
      get epoch() {
        return this.__epoch;
      }
      subscribe(handler: (event: Record<string, unknown>) => void) {
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
      async disconnect() {}
      async cancelConnect() {}
      async startMicrophone() {}
      async stopMicrophone() {}
      async sendText() {}
      async resumeAudio() {}
      async interrupt() {}
      microphoneStream() {
        // LiveKit publishes the local track asynchronously: the first meter
        // attempt after startMicrophone() must see null and the timer retries.
        if (performance.now() < this.publishedAt) return null;
        return (window as unknown as { __micStream: MediaStream }).__micStream;
      }
      remoteAudioLevel() {
        return 0.7;
      }
    }
    const runtime = new FakeRuntime();
    (window as unknown as { __speak: () => void }).__speak = () =>
      runtime.emit({ type: 'status', source: 'agent', text: 'speaking' });
    (window as unknown as { __SARJY_VOICE_RUNTIME_FACTORY__?: () => unknown }).__SARJY_VOICE_RUNTIME_FACTORY__ = () =>
      runtime;
  });

  await page.goto('/');
  await ready(page);
  const orb = page.locator('.orb-button');
  await orb.click();
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');

  await expect.poll(async () => Number(await orb.getAttribute('data-mic-level'))).toBeGreaterThan(0);

  await page.evaluate(() => (window as unknown as { __speak: () => void }).__speak());
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'speaking');
  await expect.poll(async () => Number(await orb.getAttribute('data-output-level'))).toBeCloseTo(0.7, 1);
});

async function measureLightOrb(page: Page, orb: Locator) {
  const box = await orb.boundingBox();
  if (!box) throw new Error('orb bounding box unavailable');
  const buffer = await page.screenshot({ animations: 'disabled' });
  return page.evaluate(
    async ({ b64, box }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${b64}`;
      await image.decode();
      const off = document.createElement('canvas');
      off.width = image.width;
      off.height = image.height;
      const ctx = off.getContext('2d')!;
      ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, image.width, image.height).data;
      const pixel = (x: number, y: number) => {
        const index = (y * image.width + x) * 4;
        return [data[index], data[index + 1], data[index + 2]];
      };
      const background = pixel(4, 4);
      const size = Math.min(box.width, box.height);
      const centreX = box.x + box.width / 2;
      const centreY = box.y + box.height / 2;
      const distances = (from: number, to: number) => {
        const values: number[] = [];
        for (let y = Math.floor(centreY - size / 2); y < centreY + size / 2; y += 1) {
          for (let x = Math.floor(centreX - size / 2); x < centreX + size / 2; x += 1) {
            const distance = Math.hypot(x - centreX, y - centreY) / size;
            if (distance < from || distance > to) continue;
            const [r, g, b] = pixel(x, y);
            values.push(Math.hypot(r - background[0], g - background[1], b - background[2]));
          }
        }
        values.sort((a, b) => a - b);
        return {
          median: values[Math.floor(values.length / 2)]!,
          p90: values[Math.floor(values.length * 0.9)]!,
        };
      };
      return { core: distances(0, 0.42), ring: distances(0.425, 0.445) };
    },
    { b64: buffer.toString('base64'), box },
  );
}

test('light-mode orb pixels separate from the page background', async ({ page }) => {
  await setTheme(page, 'light');
  await page.emulateMedia({ colorScheme: 'light' });
  await installFakeMicrophone(page);
  await page.goto('/');
  await ready(page);
  await page.waitForTimeout(900);
  await shot(page, 'idle-light-opaque');

  const metrics = await measureLightOrb(page, page.locator('.orb-button'));
  // The pale kit cloud sat at ~65 RGB distance from the page before the
  // light-mode depth work; an opaque, structured cloud clears 100 by a margin.
  expect(metrics.core.median).toBeGreaterThan(100);
  expect(metrics.ring.p90).toBeGreaterThan(75);
});

test('the Canvas2D light orb is equally opaque and structured', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sarjy-theme', 'light');
    localStorage.setItem('sarjy-language', 'en');
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...args: unknown[]) {
      if (type === 'webgl2') return null;
      return (original as unknown as (this: HTMLCanvasElement, type: string, ...args: unknown[]) => unknown).call(
        this,
        type,
        ...args,
      );
    } as typeof HTMLCanvasElement.prototype.getContext;
  });
  await page.goto('/');
  const orb = page.locator('.orb-button');
  await expect(orb).toHaveAttribute('data-renderer', 'light');
  await page.waitForTimeout(900);
  await shot(page, 'idle-light-canvas');

  const metrics = await measureLightOrb(page, orb);
  expect(metrics.core.median).toBeGreaterThan(100);
});

test('capture idle evidence at 1440x900 in both themes', async ({ page }) => {
  await setTheme(page, 'dark');
  await installFakeMicrophone(page);
  await page.goto('/');
  await ready(page);
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'idle');
  await page.waitForTimeout(700);
  await shot(page, 'idle-dark');

  await setTheme(page, 'light');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/');
  await ready(page);
  await page.waitForTimeout(700);
  await shot(page, 'idle-light');
});
