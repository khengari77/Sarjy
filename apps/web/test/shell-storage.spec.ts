import { expect, test, type Page } from '@playwright/test';

// Storage-failure regressions. Blocked storage must never abort the shell:
// neither a throwing `localStorage` getter, nor a storage object whose methods
// throw. Kept separate from the drawer specs so the failure class is obvious.

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
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  throw new Error('shell never hydrated');
}

async function installStorageStub(page: Page, mode: 'read-throws' | 'write-quota') {
  await page.addInitScript((stubMode) => {
    const store = new Map<string, string>([['sarjy-theme', 'dark']]);
    const stub = {
      get length() {
        return store.size;
      },
      key: (index: number) => [...store.keys()][index] ?? null,
      getItem(key: string) {
        if (stubMode === 'read-throws') throw new DOMException('blocked', 'SecurityError');
        return store.get(key) ?? null;
      },
      setItem(key: string, value: string) {
        if (stubMode === 'write-quota') throw new DOMException('full', 'QuotaExceededError');
        store.set(key, value);
      },
      removeItem(key: string) {
        store.delete(key);
      },
      clear() {
        store.clear();
      },
    };
    Object.defineProperty(window, 'localStorage', { configurable: true, value: stub });
  }, mode);
}

test('a storage object whose getItem throws does not abort shell initialisation', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await installStorageStub(page, 'read-throws');

  await page.setViewportSize({ width: 1280, height: 720 });
  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  await waitForShell(page);

  const leftPanel = page.locator('#shell-left');
  await expect(leftPanel).toBeVisible();
  await expect(leftPanel).toHaveAttribute('data-hydrated', 'true');
  expect(await leftPanel.evaluate((el) => (el as HTMLElement).inert)).toBe(false);

  // Unreadable storage degrades to the system preference...
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'system');
  // ...and a failing write is non-fatal: the preference still advances.
  const theme = page.getByRole('button', { name: /^Theme:/ });
  await theme.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'light');
  expect(errors.filter((message) => /SecurityError|QuotaExceededError/.test(message))).toEqual([]);
});

test('a storage object whose setItem throws (quota) is non-fatal', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await installStorageStub(page, 'write-quota');

  await page.setViewportSize({ width: 1280, height: 720 });
  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  await waitForShell(page);

  // Reads still work: the stub persisted 'dark'.
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'dark');
  await expect(page.locator('#shell-left')).toHaveAttribute('data-hydrated', 'true');

  const theme = page.getByRole('button', { name: /^Theme:/ });
  await theme.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'system');
  await theme.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'light');
  expect(errors.filter((message) => /SecurityError|QuotaExceededError/.test(message))).toEqual([]);
});

test('blocked storage does not abort shell initialisation', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('storage blocked for test', 'SecurityError');
      },
    });
  });

  await page.setViewportSize({ width: 1280, height: 720 });
  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  await waitForShell(page);

  const leftPanel = page.locator('#shell-left');
  await expect(leftPanel).toBeVisible();
  await expect(leftPanel).toHaveAttribute('data-hydrated', 'true');
  expect(await leftPanel.evaluate((el) => (el as HTMLElement).inert)).toBe(false);

  // The theme control still works without storage; persistence is best effort.
  const theme = page.getByRole('button', { name: /^Theme:/ });
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'system');
  await theme.click();
  // Preference advances even though nothing could be persisted.
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'light');
  expect(errors.filter((message) => /SecurityError|storage blocked/.test(message))).toEqual([]);
});
