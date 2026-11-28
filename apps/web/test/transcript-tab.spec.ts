import { expect, test, type Page } from '@playwright/test';

type Turns = { id: string; role: string; text: string; status: string }[];

async function mockData(page: Page, turns: Turns) {
  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'owner' } }));
  await page.route('**/api/v1/conversations', (route) =>
    route.fulfill({ json: [{ id: 'c1', title: 'Riyadh weather', revision: 0 }] }),
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: turns }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/settings', (route) =>
    route.fulfill({
      json: {
        speaking_speed: 1,
        captions_visible: true,
        approved_voice_id: null,
        response_preference: 'automatic',
        revision: 0,
      },
    }),
  );
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('only interrupted or failed turns show a status in the transcript', async ({ page }) => {
  await mockData(page, [
    { id: 't1', role: 'user', text: 'Tell me a story', status: 'completed' },
    { id: 't2', role: 'assistant', text: 'Once upon a', status: 'interrupted' },
    { id: 't3', role: 'assistant', text: 'Sorry', status: 'failed' },
  ]);
  await page.goto('/');
  await page.locator('.caption-bubble').click();
  const panel = page.locator('#drawer-panel-transcript');
  await expect(panel.locator('.turn').nth(0).locator('small')).toHaveText('You');
  await expect(panel.locator('.turn').nth(1).locator('small')).toHaveText('Sarjy · Interrupted');
  await expect(panel.locator('.turn').nth(2).locator('small')).toHaveText('Sarjy · Failed');
});

test('a long reply never moves the orb or the composer', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockData(page, [
    { id: 't1', role: 'user', text: 'Hi', status: 'completed' },
    { id: 't2', role: 'assistant', text: 'Short.', status: 'completed' },
  ]);
  await page.goto('/');
  const geometry = () =>
    page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)?.getBoundingClientRect();
      return {
        orb: box('.orb-button')?.top ?? -1,
        bubble: box('.caption-bubble')?.top ?? -1,
        bubbleHeight: box('.caption-bubble')?.height ?? -1,
        composer: box('.composer')?.top ?? -1,
      };
    });
  await expect(page.locator('.caption-bubble')).toContainText('Short.');
  const before = await geometry();
  const longReply = 'A very long reply. '.repeat(60);
  await page.route('**/api/v1/conversations/*/turns', (route) =>
    route.fulfill({
      json: [
        { id: 't1', role: 'user', text: 'Hi', status: 'completed' },
        { id: 't2', role: 'assistant', text: longReply, status: 'completed' },
      ],
    }),
  );
  await page.reload();
  await expect(page.locator('.caption-bubble')).toContainText('A very long reply.');
  // The accessible name is a bounded excerpt, not a second copy of the whole
  // reply (the live region already announces final turns).
  const label = await page.locator('.caption-bubble').getAttribute('aria-label');
  expect(label).toContain('Transcript: ');
  expect(label?.endsWith('…'), 'long excerpt ends with an ellipsis').toBe(true);
  expect(label!.length).toBeLessThanOrEqual('Transcript: '.length + 201);
  expect(label!.length).toBeLessThan(longReply.length);

  const after = await geometry();
  // The caption may grow to its three-line cap, but neither it nor the reply
  // length moves the orb or the composer.
  expect(after.orb).toBe(before.orb);
  expect(after.composer).toBe(before.composer);
  expect(after.bubble).toBe(before.bubble);
  expect(after.bubbleHeight).toBeGreaterThan(before.bubbleHeight);
  expect(after.bubbleHeight).toBeLessThanOrEqual(92);
});
