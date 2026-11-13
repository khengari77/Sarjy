import { expect, test, type Page } from '@playwright/test';
import { parseHexColor } from '../src/lib/orb/render/colors';

const MUTED_PATH = '/?phase=speaking&overlay=muted';

async function forceCanvas2d(page: Page) {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...args: unknown[]) {
      if (type === 'webgl2') return null;
      return (original as (...params: unknown[]) => unknown).call(this, type, ...args);
    } as typeof HTMLCanvasElement.prototype.getContext;
  });
}

async function assertLiveColor(value: string) {
  const channels = parseHexColor(value);
  expect(channels, value).not.toBeNull();
  expect(channels!.every((channel) => Number.isFinite(channel) && channel >= 0 && channel <= 1)).toBe(true);
  expect(channels!.reduce((sum, channel) => sum + channel, 0)).toBeGreaterThan(0.3);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
  });
});

test('enhanced WebGL muted speaking renders a supported non-black colour', async ({ page }) => {
  await page.goto(MUTED_PATH);
  const orb = page.locator('.orb-button');
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');
  await expect(page.locator('.status-line')).toContainText('Speaking');
  await expect(page.locator('.mic-slash')).toBeVisible();
  await assertLiveColor((await page.locator('canvas').getAttribute('data-color')) ?? '');
});

test('light Canvas2D muted speaking renders a supported non-black colour', async ({ page }) => {
  await forceCanvas2d(page);
  await page.goto(MUTED_PATH);
  const orb = page.locator('.orb-button');
  await expect(orb).toHaveAttribute('data-renderer', 'light');
  await expect(page.locator('.status-line')).toContainText('Speaking');
  await expect(page.locator('.mic-slash')).toBeVisible();
  await assertLiveColor((await page.locator('canvas').getAttribute('data-color')) ?? '');
});

test('static reduced-motion muted thinking renders a supported non-black colour', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/?phase=thinking&overlay=muted');
  const orb = page.locator('.orb-button');
  await expect(orb).toHaveAttribute('data-renderer', 'static');
  await expect(page.locator('.status-line')).toContainText('Thinking');
  await expect(page.locator('.mic-slash')).toBeVisible();
  const color = await page
    .locator('.orb-static')
    .evaluate((element) => getComputedStyle(element).getPropertyValue('--orb-color').trim());
  await assertLiveColor(color);
});
