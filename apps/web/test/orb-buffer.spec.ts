import { expect, test, type Page } from '@playwright/test';

declare global {
  interface Window {
    __bufferWrites?: number;
  }
}

async function countOrbBufferWrites(page: Page) {
  await page.addInitScript(() => {
    window.__bufferWrites = 0;
    const prototype = HTMLCanvasElement.prototype;
    const width = Object.getOwnPropertyDescriptor(prototype, 'width')!;
    const height = Object.getOwnPropertyDescriptor(prototype, 'height')!;
    Object.defineProperty(prototype, 'width', {
      configurable: true,
      get: width.get,
      set(value: number) {
        if (this.classList?.contains('orb-canvas')) window.__bufferWrites = (window.__bufferWrites ?? 0) + 1;
        width.set!.call(this, value);
      },
    });
    Object.defineProperty(prototype, 'height', {
      configurable: true,
      get: height.get,
      set(value: number) {
        if (this.classList?.contains('orb-canvas')) window.__bufferWrites = (window.__bufferWrites ?? 0) + 1;
        height.set!.call(this, value);
      },
    });
  });
}

async function writes(page: Page) {
  return page.evaluate(() => window.__bufferWrites ?? 0);
}

test('WebGL frames do not reset the backing buffer when dimensions are unchanged', async ({ page }) => {
  await countOrbBufferWrites(page);
  await page.goto('/');
  const orb = page.locator('.orb-button');
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');
  await expect.poll(() => writes(page)).toBeGreaterThanOrEqual(2);

  const settled = await writes(page);
  await page.waitForTimeout(600);
  expect(await writes(page)).toBe(settled);

  await page.setViewportSize({ width: 1100, height: 800 });
  await expect.poll(() => writes(page)).toBeGreaterThan(settled);
});

test('Canvas2D frames do not reset the backing buffer when dimensions are unchanged', async ({ page }) => {
  await countOrbBufferWrites(page);
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...args: unknown[]) {
      if (type === 'webgl2') return null;
      return (original as (...params: unknown[]) => unknown).call(this, type, ...args);
    } as typeof HTMLCanvasElement.prototype.getContext;
  });
  await page.goto('/');
  const orb = page.locator('.orb-button');
  await expect(orb).toHaveAttribute('data-renderer', 'light');
  await expect.poll(() => writes(page)).toBeGreaterThanOrEqual(2);

  const settled = await writes(page);
  await page.waitForTimeout(600);
  expect(await writes(page)).toBe(settled);

  await page.setViewportSize({ width: 1100, height: 800 });
  await expect.poll(() => writes(page)).toBeGreaterThan(settled);
});
