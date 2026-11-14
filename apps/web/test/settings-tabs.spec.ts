import { expect, test, type Page } from '@playwright/test';

// The right drawer has three panels, one concern each. Controls holds every
// interactive setting we actually have: voice, speed and captions. Memory
// search lives only in Memories; Context is the compiled-context inspector.
// Appearance was removed because theme lives in the header and captions moved
// to Controls, leaving the panel empty.

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

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('controls keeps voice, speed and captions together without memory search', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await waitForShell(page);
  await page.getByRole('button', { name: 'Show memory and settings' }).click();

  const drawer = page.locator('#shell-right');
  const tabs = drawer.getByRole('tab');
  await expect(tabs).toHaveText(['Memories', 'Controls', 'Context']);
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');

  const controls = page.locator('#drawer-panel-controls');
  await expect(controls.getByLabel('Choose voice')).toBeVisible();
  await expect(controls.getByLabel('Speaking speed')).toBeVisible();
  await expect(controls.getByRole('checkbox', { name: 'Show captions' })).toBeVisible();
  await expect(controls).toContainText('Voice changes apply to the next call.');
  await expect(controls).toContainText('Speed applies to the next turn in the call.');
  await expect(controls).not.toContainText('Memory & context');
  await expect(controls).not.toContainText('Search saved facts');

  const memories = page.locator('#drawer-panel-memories');
  await drawer.getByRole('tab', { name: 'Memories' }).click();
  await expect(memories).toContainText('Memory & context');
  await expect(memories.getByRole('search')).toBeVisible();
  await expect(memories).not.toContainText('Choose voice');

  await drawer.getByRole('tab', { name: 'Context' }).click();
  await expect(page.locator('#drawer-panel-context')).toContainText('general context inspector arrives in #46');
});

test('speed uses the real settings endpoint and applies to the next turn', async ({ page }) => {
  let revision = 0;
  let speed = 1;
  const writes: unknown[] = [];
  const settings = () => ({
    speaking_speed: speed,
    captions_visible: true,
    approved_voice_id: null,
    response_preference: 'automatic',
    revision,
  });
  await page.route('**/api/v1/settings', (route) => route.fulfill({ json: settings() }));
  await page.route('**/api/v1/settings/actions', async (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    expect(body.action).toEqual({ kind: 'SetSpeakingSpeed', speed: 1.2 });
    expect(body.request_id).toBe('speed:0:1.2');
    speed = body.action.speed;
    revision += 1;
    await route.fulfill({ json: { kind: 'SetSpeakingSpeed', settings: settings(), changed: true } });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await waitForShell(page);
  await page.getByRole('button', { name: 'Show memory and settings' }).click();

  const select = page.getByLabel('Speaking speed');
  await expect(select).toHaveValue('1');
  await select.selectOption('1.2');
  await expect.poll(() => writes.length).toBe(1);
  await expect(select).toHaveValue('1.2');
  await expect(page.locator('#drawer-panel-controls')).toContainText('Speed applies to the next turn in the call.');
});

test('arrow, Home and End still move between the renamed tabs', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await waitForShell(page);
  await page.getByRole('button', { name: 'Show memory and settings' }).click();

  const tabs = page.locator('#shell-right').getByRole('tab');
  await tabs.nth(0).focus();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.nth(1)).toBeFocused();
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('End');
  await expect(tabs.nth(2)).toBeFocused();
  await expect(tabs.nth(2)).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('Home');
  await expect(tabs.nth(0)).toBeFocused();
  await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('ArrowLeft');
  await expect(tabs.nth(2)).toBeFocused();
});
