import { expect, test, type Page } from '@playwright/test';

interface CloudStats {
  center: number;
  rim: number;
  spread: number;
}

/**
 * Decode a Playwright PNG inside the browser and measure how far the orb's
 * centre and rim sit from the page background. A hollow shell leaves the
 * centre at background distance ~0 while the rim stays coloured.
 */
async function cloudStats(page: Page, png: Buffer): Promise<CloudStats> {
  return page.evaluate(async (encoded) => {
    const response = await fetch(`data:image/png;base64,${encoded}`);
    const bitmap = await createImageBitmap(await response.blob());
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('no 2d context');
    ctx.drawImage(bitmap, 0, 0);
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);

    const pixel = (x: number, y: number, offset: number) => {
      const index = (y * canvas.width + x) * 4 + offset;
      return data[index] ?? 0;
    };
    const corner = (x0: number, y0: number) => {
      const total = [0, 0, 0];
      let count = 0;
      for (let y = y0; y < y0 + 8; y += 1) {
        for (let x = x0; x < x0 + 8; x += 1) {
          total[0] += pixel(x, y, 0);
          total[1] += pixel(x, y, 1);
          total[2] += pixel(x, y, 2);
          count += 1;
        }
      }
      return total.map((value) => value / count);
    };
    const backgrounds = [
      corner(0, 0),
      corner(canvas.width - 8, 0),
      corner(0, canvas.height - 8),
      corner(canvas.width - 8, canvas.height - 8),
    ];
    const background = [0, 1, 2].map(
      (channel) => backgrounds.reduce((sum, value) => sum + (value[channel] ?? 0), 0) / backgrounds.length,
    );

    const scale = Math.min(canvas.width, canvas.height);
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const sample = (inner: number, outer: number) => {
      let total = 0;
      let count = 0;
      for (let y = 0; y < canvas.height; y += 1) {
        for (let x = 0; x < canvas.width; x += 1) {
          const distance = Math.hypot(x - centerX, y - centerY) / scale;
          if (distance < inner || distance > outer) continue;
          const dr = pixel(x, y, 0) - (background[0] ?? 0);
          const dg = pixel(x, y, 1) - (background[1] ?? 0);
          const db = pixel(x, y, 2) - (background[2] ?? 0);
          total += Math.hypot(dr, dg, db);
          count += 1;
        }
      }
      return count ? total / count : 0;
    };

    return {
      center: sample(0, 0.14),
      rim: sample(0.31, 0.4),
      spread: scale,
    };
  }, png.toString('base64'));
}

/**
 * Stamp `data-theme` before styles apply. Reading the query string keeps a
 * single init script valid across navigations (registering one per theme would
 * leave the first theme's script running for later loads).
 */
async function installTheme(page: Page) {
  await page.addInitScript(() => {
    const apply = () => {
      const value = new URLSearchParams(location.search).get('theme');
      if (value === 'light' || value === 'dark') document.documentElement.dataset.theme = value;
    };
    if (document.documentElement) apply();
    else document.addEventListener('DOMContentLoaded', apply, { once: true });
  });
}

test.describe('Living Cloud fill and motion', () => {
  test('enhanced WebGL orb is filled at the centre in both themes', async ({ page }) => {
    await installTheme(page);
    const centres: Record<string, number> = {};
    for (const theme of ['light', 'dark'] as const) {
      await page.goto(`/?theme=${theme}`);
      const orb = page.getByRole('button', { name: 'Start voice conversation' });
      await expect(orb).not.toHaveAttribute('data-renderer', 'static', { timeout: 60000 });
      // The theme must reach the renderer, not just the page chrome.
      await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe(theme);
      await page.waitForTimeout(700);
      const stats = await cloudStats(page, await orb.screenshot());
      expect(stats.spread).toBeGreaterThan(150);
      expect(stats.center, `${theme} centre must not be hollow`).toBeGreaterThan(12);
      expect(stats.center, `${theme} centre must retain body density`).toBeGreaterThan(stats.rim * 0.55);
      centres[theme] = stats.center;
    }
    // Cosmic Cloud is a high-contrast body on a near-black page; Sky Prism is
    // pastel on a pale page. Identical numbers would mean the palette never
    // switched with the theme.
    expect(centres.dark).toBeGreaterThan((centres.light ?? 0) + 40);
  });

  test('WebGL interior billows move between frames', async ({ page }) => {
    await page.goto('/');
    const orb = page.getByRole('button', { name: 'Start voice conversation' });
    await expect(orb).not.toHaveAttribute('data-renderer', 'static', { timeout: 60000 });
    await page.waitForTimeout(800);
    const first = await orb.screenshot();
    await page.waitForTimeout(1200);
    const second = await orb.screenshot();
    expect(first.equals(second)).toBe(false);

    const changed = await page.evaluate(
      async (encoded) => {
        const decode = async (value: string) => {
          const response = await fetch(`data:image/png;base64,${value}`);
          const bitmap = await createImageBitmap(await response.blob());
          const canvas = document.createElement('canvas');
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
          const ctx = canvas.getContext('2d');
          if (!ctx) throw new Error('no 2d context');
          ctx.drawImage(bitmap, 0, 0);
          return {
            data: ctx.getImageData(0, 0, canvas.width, canvas.height).data,
            width: canvas.width,
            height: canvas.height,
          };
        };
        const a = await decode(encoded[0]);
        const b = await decode(encoded[1]);
        let count = 0;
        for (let y = 0; y < a.height; y += 1) {
          for (let x = 0; x < a.width; x += 1) {
            if (Math.hypot(x - a.width / 2, y - a.height / 2) > Math.min(a.width, a.height) * 0.34) continue;
            const index = (y * a.width + x) * 4;
            if (Math.abs((a.data[index] ?? 0) - (b.data[index] ?? 0)) > 6) count += 1;
          }
        }
        return count;
      },
      [first.toString('base64'), second.toString('base64')],
    );
    expect(changed).toBeGreaterThan(80);
  });

  test('Canvas2D fallback is filled, not a ring', async ({ page }) => {
    await page.addInitScript(() => {
      const original = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...args: unknown[]) {
        if (type === 'webgl2') return null;
        return (original as (...params: unknown[]) => unknown).call(this, type, ...args);
      } as typeof HTMLCanvasElement.prototype.getContext;
    });
    await page.goto('/');
    const orb = page.locator('.orb-button');
    await expect(orb).toHaveAttribute('data-renderer', 'light', { timeout: 60000 });
    await page.waitForTimeout(700);
    const stats = await cloudStats(page, await orb.screenshot());
    expect(stats.center).toBeGreaterThan(10);
    expect(stats.center).toBeGreaterThan(stats.rim * 0.5);
  });
});

test('a low-FPS downgrade reaches the Canvas2D tier instead of collapsing to static', async ({ page }) => {
  // ~18 fps: above the 12 fps static floor, below the 30 fps enhanced floor,
  // so the downward ladder must choose 'light'.
  await page.addInitScript(() => {
    let nextId = 1;
    const timers = new Map<number, number>();
    window.requestAnimationFrame = (callback: FrameRequestCallback) => {
      const id = nextId++;
      const timer = window.setTimeout(() => {
        timers.delete(id);
        callback(performance.now());
      }, 55);
      timers.set(id, timer);
      return id;
    };
    window.cancelAnimationFrame = (id: number) => {
      const timer = timers.get(id);
      if (timer !== undefined) {
        window.clearTimeout(timer);
        timers.delete(id);
      }
    };
  });
  await page.goto('/?theme=light&phase=idle');
  const orb = page.locator('.orb-button');
  await expect(orb).not.toHaveAttribute('data-renderer', 'static', { timeout: 60000 });
  await expect(orb, 'FPS downgrade must land on the Canvas2D tier').toHaveAttribute('data-renderer', 'light', {
    timeout: 30000,
  });
  await expect(orb.locator('.orb-static')).toHaveCount(0);
  await expect(orb.locator('canvas.orb-canvas')).toHaveCount(1);
  const isCanvas2d = await page.evaluate(() => {
    const surface = document.querySelector<HTMLCanvasElement>('canvas.orb-canvas');
    return surface ? surface.getContext('2d') !== null : false;
  });
  expect(isCanvas2d, 'light tier must own a real 2D surface').toBe(true);
  await page.waitForTimeout(700);
  const stats = await cloudStats(page, await orb.screenshot());
  expect(stats.center, 'Canvas2D downgrade must still draw a filled cloud').toBeGreaterThan(10);
  expect(stats.center).toBeGreaterThan(stats.rim * 0.5);
  await expect(page.locator('.status-line')).toHaveText(/Ready/);
});

test('reduced-motion static fallback stays filled and labelled', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toHaveAttribute('data-renderer', 'static');
  await expect(orb.locator('.orb-static')).toHaveCount(1);
  await expect(orb.locator('.orb-state-text')).toHaveText('Ready');
  const stats = await cloudStats(page, await orb.screenshot());
  expect(stats.center, 'static fallback must still show cloud').toBeGreaterThan(8);

  const first = await orb.screenshot();
  await page.waitForTimeout(600);
  const second = await orb.screenshot();
  expect(first.equals(second)).toBe(true);
});
