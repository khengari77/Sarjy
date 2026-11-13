import { expect, test, type Page } from '@playwright/test';

test.use({ colorScheme: 'dark' });

function themeButton(page: Page) {
  return page.getByRole('button', { name: /^Theme:/ });
}

async function waitForOrb(page: Page) {
  // SSR renders the orb statically; waiting for the hydrated renderer avoids
  // clicking header controls before Svelte attaches their handlers.
  await expect(page.locator('.orb-button')).toBeVisible();
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('theme control cycles light/dark/system without creating a voice session or recreating the orb', async ({
  page,
}) => {
  const sessionPosts: string[] = [];
  page.on('request', (request) => {
    const { pathname } = new URL(request.url());
    if (request.method() === 'POST' && pathname.endsWith('/api/v1/voice/sessions')) sessionPosts.push(pathname);
  });

  await page.goto('/');
  await waitForOrb(page);
  const html = page.locator('html');
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(html).toHaveAttribute('data-theme-preference', 'system');

  await page.evaluate(() => {
    (window as Window & { __orbCanvas?: Element | null }).__orbCanvas = document.querySelector('.orb-button canvas');
  });

  // system → light
  await themeButton(page).click();
  await expect(html).toHaveAttribute('data-theme', 'light');
  await expect(html).toHaveAttribute('data-theme-preference', 'light');
  expect(await page.evaluate(() => localStorage.getItem('sarjy-theme'))).toBe('light');
  await expect(themeButton(page)).toHaveAccessibleName('Theme: Light. Activate to switch to Dark.');

  // light → dark
  await themeButton(page).click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  expect(await page.evaluate(() => localStorage.getItem('sarjy-theme'))).toBe('dark');

  // dark → system
  await themeButton(page).click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(html).toHaveAttribute('data-theme-preference', 'system');
  expect(await page.evaluate(() => localStorage.getItem('sarjy-theme'))).toBe('system');

  expect(sessionPosts).toEqual([]);
  expect(
    await page.evaluate(
      () =>
        (window as Window & { __orbCanvas?: Element | null }).__orbCanvas ===
        document.querySelector('.orb-button canvas'),
    ),
  ).toBe(true);
});

test('a saved theme applies before paint on reload and SSR never stores it', async ({ page, request }) => {
  const serverHtml = await (await request.get('/')).text();
  expect(serverHtml).not.toMatch(/<html[^>]*data-theme=/);
  expect(serverHtml).not.toMatch(/<html[^>]*data-theme-preference=/);

  await page.goto('/');
  await waitForOrb(page);
  await themeButton(page).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'light');
});

test('desktop starts with left open, right closed and the orb and composer fit 1280×720', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForOrb(page);

  await expect(page.getByRole('button', { name: 'Show conversations' })).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('button', { name: 'Show memory and settings' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  await expect(page.locator('#shell-left')).toBeVisible();
  await expect(page.locator('#shell-right')).toBeHidden();

  await expect(page.locator('.orb-button')).toBeVisible();
  await expect(page.locator('footer textarea')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 1)).toBe(true);
});

test('tablet and mobile start with both drawers closed and keep the triggers reachable', async ({ page }) => {
  for (const viewport of [
    { width: 900, height: 720 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await waitForOrb(page);

    const left = page.getByRole('button', { name: 'Show conversations' });
    const right = page.getByRole('button', { name: 'Show memory and settings' });
    await expect(left).toBeVisible();
    await expect(right).toBeVisible();
    await expect(left).toHaveAttribute('aria-expanded', 'false');
    await expect(right).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#shell-left')).toBeHidden();
    await expect(page.locator('#shell-right')).toBeHidden();
  }
});

test('desktop docks toggle independently', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForOrb(page);

  const left = page.getByRole('button', { name: 'Show conversations' });
  const right = page.getByRole('button', { name: 'Show memory and settings' });
  await right.click();
  await expect(left).toHaveAttribute('aria-expanded', 'true');
  await right.click();
  await expect(right).toHaveAttribute('aria-expanded', 'false');
  await expect(left).toHaveAttribute('aria-expanded', 'true');
  await left.click();
  await expect(left).toHaveAttribute('aria-expanded', 'false');
});

test('the right drawer exposes the four tabs, closes on Escape and returns focus', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForOrb(page);

  const trigger = page.getByRole('button', { name: 'Show memory and settings' });
  await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#shell-right')).toBeVisible();
  // On desktop the right panel docks next to the still-open left panel.
  await expect(page.getByRole('button', { name: 'Show conversations' })).toHaveAttribute('aria-expanded', 'true');

  const tabs = page.locator('#shell-right').getByRole('tab');
  await expect(tabs).toHaveText(['Memories', 'Voice', 'Appearance', 'Context']);
  await page.locator('#shell-right').getByRole('tab', { name: 'Memories' }).click();
  await expect(page.locator('#shell-right')).toContainText('memory explorer arrives in #43');

  await page.keyboard.press('Escape');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).toBeFocused();
});
