import { expect, test, type Page } from '@playwright/test';

test.use({ colorScheme: 'dark' });

function themeButton(page: Page) {
  return page.getByRole('button', { name: /^Theme:/ });
}

function channel(value: string, index: number): number {
  return parseInt(value.replace('#', '').slice(index * 2, index * 2 + 2), 16) / 255;
}

function luminance(hex: string): number {
  const [r, g, b] = [0, 1, 2].map((index) => channel(hex, index));
  const linear = (value: number) => (value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

function contrast(foreground: string, background: string): number {
  const [hi, lo] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
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
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
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
  expect(serverHtml).toMatch(/<html lang="en"/);
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

test('functional tokens meet measured contrast in both themes', async ({ page }) => {
  await page.goto('/');
  await waitForOrb(page);

  const tokenNames = [
    '--bg-app',
    '--bg-sidebar',
    '--surface-elevated',
    '--surface-hover',
    '--border-subtle',
    '--focus-ring',
    '--text-primary',
    '--text-muted',
    '--accent',
    '--status-success',
    '--status-error',
  ] as const;

  const evidence: Record<string, Record<string, string>> = {};
  for (const theme of ['dark', 'light'] as const) {
    const tokens = await page.evaluate(
      ({ theme, names }) => {
        document.documentElement.setAttribute('data-theme', theme);
        document.documentElement.setAttribute('data-theme-preference', theme);
        const style = getComputedStyle(document.documentElement);
        return Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name).trim()]));
      },
      { theme, names: [...tokenNames] },
    );
    evidence[theme] = tokens;

    const borderSurfaces = ['--bg-app', '--bg-sidebar', '--surface-elevated', '--surface-hover'];
    for (const surface of borderSurfaces) {
      expect(
        contrast(tokens['--border-subtle'], tokens[surface]),
        `${theme}: --border-subtle ${tokens['--border-subtle']} on ${surface} ${tokens[surface]}`,
      ).toBeGreaterThanOrEqual(3);
    }

    const functional: Array<[string, number]> = [
      ['--focus-ring', 3],
      ['--accent', 3],
      ['--status-success', 3],
      ['--status-error', 3],
    ];
    for (const [token, minimum] of functional) {
      for (const surface of ['--bg-app', '--bg-sidebar', '--surface-elevated']) {
        expect(
          contrast(tokens[token], tokens[surface]),
          `${theme}: ${token} ${tokens[token]} on ${surface} ${tokens[surface]}`,
        ).toBeGreaterThanOrEqual(minimum);
      }
    }

    for (const token of ['--text-primary', '--text-muted']) {
      for (const surface of ['--bg-app', '--bg-sidebar', '--surface-elevated', '--surface-hover']) {
        expect(
          contrast(tokens[token], tokens[surface]),
          `${theme}: ${token} ${tokens[token]} on ${surface} ${tokens[surface]}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
  }

  await test.info().attach('contrast-evidence.json', {
    body: JSON.stringify(evidence, null, 2),
    contentType: 'application/json',
  });
});

test('tablet drawers overlay without squeezing the orb', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 720 });
  await page.goto('/');
  await waitForOrb(page);
  const orbWidth = () => page.locator('.orb-button').evaluate((el) => el.getBoundingClientRect().width);
  const before = await orbWidth();

  await page.getByRole('button', { name: 'Show conversations' }).click();
  await expect(page.locator('#shell-left')).toBeVisible();
  await expect(page.locator('.scrim')).toBeVisible();
  expect(await orbWidth()).toBe(before);
  expect(before).toBeGreaterThanOrEqual(220);

  // The call-control strip sits above the scrim (z 26 vs 25) so Mute/End stay
  // clickable while a drawer is open (#37). Its bottom band therefore does not
  // dismiss the drawer; the scrim above it still does.
  const strip = await page.locator('.stage-controls').boundingBox();
  expect(strip).not.toBeNull();
  expect(strip!.y).toBeLessThan(720);
  await page.mouse.click(880, (strip!.y + 720) / 2);
  await expect(page.getByRole('button', { name: 'Show conversations' })).toHaveAttribute('aria-expanded', 'true');

  await page.locator('.scrim').click({ position: { x: 880, y: 300 } });
  await expect(page.getByRole('button', { name: 'Show conversations' })).toHaveAttribute('aria-expanded', 'false');
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
  await page.locator('#shell-right').getByRole('tab', { name: 'Context' }).click();
  await expect(page.locator('#shell-right')).toContainText('context inspector arrives in #43/#46');

  await page.keyboard.press('Escape');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).toBeFocused();
});
