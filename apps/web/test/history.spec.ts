import { expect, test } from '@playwright/test';
import {
  allowMicrophone,
  expectSelectionBackground,
  gotoHistory,
  installFakeRuntime,
  mockHistory,
  mockSessions,
} from './support/historyHarness';

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('search filters by title client-side and reports no matches', async ({ page }) => {
  const mocked = await mockHistory(page);
  await gotoHistory(page);
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible();

  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('paris');
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Work ideas', exact: true })).toHaveCount(0);
  expect(mocked.listCalls()).toBe(1);

  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('zzz');
  await expect(page.getByText('No matching conversations.')).toBeVisible();
});

test('Arabic titles match without diacritics or hamza variants', async ({ page }) => {
  await mockHistory(page);
  await gotoHistory(page);
  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('ميزانيه');
  await expect(page.getByRole('button', { name: 'ميزانية Tripoli', exact: true })).toBeVisible();
});

test('the selected conversation exposes aria-current and a visible selection', async ({ page }) => {
  await mockHistory(page);
  await gotoHistory(page);

  const first = page.getByRole('button', { name: 'Trip to Paris', exact: true });
  await expect(first).toHaveAttribute('aria-current', 'page');
  await expectSelectionBackground(page, first);

  const second = page.getByRole('button', { name: 'Work ideas', exact: true });
  await second.click();
  await expect(second).toHaveAttribute('aria-current', 'page');
  await expect(first).not.toHaveAttribute('aria-current', 'page');
  await expectSelectionBackground(page, second);
});

test('conversation menus dismiss on Escape or outside interaction and restore focus', async ({ page }) => {
  await mockHistory(page);
  await gotoHistory(page);

  const trigger = page.getByRole('button', { name: 'Conversation actions for Trip to Paris' });
  const menu = page.getByRole('menu');

  await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(menu).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();

  await trigger.click();
  await expect(menu).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search conversations' }).click();
  await expect(menu).toHaveCount(0);

  // After dismissal the rows are interactive again.
  await page.getByRole('button', { name: 'Work ideas', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Work ideas', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('resuming a conversation loads turns without starting a microphone session', async ({ page }) => {
  const mocked = await mockHistory(page);
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionPosts += 1;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });
  await gotoHistory(page);
  await page.getByRole('button', { name: 'Work ideas', exact: true }).click();
  await expect.poll(() => mocked.turns.length).toBeGreaterThanOrEqual(2);
  expect(sessionPosts).toBe(0);
  await expect(page.locator('.orb-button')).toBeEnabled();
});

test('an empty list shows the filled new-chat action, always-on search and workspace footer', async ({ page }) => {
  await mockHistory(page, []);
  await gotoHistory(page);

  const newChat = page.getByRole('button', { name: 'New conversation', exact: true });
  await expect(newChat).toBeVisible();
  // The primary action is a filled accent control, not a ghost button.
  expect(await newChat.evaluate((element) => getComputedStyle(element).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');

  // Search is present even with no conversations to filter.
  await expect(page.getByRole('searchbox', { name: 'Search conversations' })).toBeVisible();
  await expect(page.locator('.sidebar-label')).toHaveText('RECENT');

  await expect(page.getByText('No conversations yet', { exact: true })).toBeVisible();
  await expect(page.getByText('Your chats will appear here.')).toBeVisible();
  await expect(page.getByText('Start with your voice')).toBeVisible();
  await expect(page.getByText('Or use the message box.')).toBeVisible();

  const footer = page.locator('.sidebar-foot');
  await expect(footer).toContainText(/S\s*Your workspace/);
  const credit = footer.getByRole('link', { name: 'Weather data: Open-Meteo' });
  await expect(credit).toHaveAttribute('href', 'https://open-meteo.com/');
  await expect(footer).toContainText('Voice Context Studio');
});

test('a list failure stays inside the sidebar and leaves the orb idle', async ({ page }) => {
  await page.route('**/api/v1/conversations', async (route) => {
    await route.fulfill({ status: 503, json: { detail: { code: 'API_UNAVAILABLE' } } });
  });
  await page.route('**/api/v1/demo-session', async (route) => route.fulfill({ json: { id: 'owner' } }));
  await gotoHistory(page);
  await expect(page.locator('[data-code="API_UNAVAILABLE"]')).toContainText('Could not load conversations');
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'idle');
});

test('switching chats mid-call requires an explicit end and switch', async ({ page }) => {
  await mockHistory(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  const ends = await mockSessions(page);

  await gotoHistory(page);
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  // A live call for this chat blocks deletion until it ends.
  await page.getByRole('button', { name: 'Conversation actions for Trip to Paris' }).click();
  await expect(page.getByRole('menu').getByRole('menuitem', { name: 'Delete' })).toBeDisabled();
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Work ideas', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'End the current voice session?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
  expect(ends).toHaveLength(0);

  await page.getByRole('button', { name: 'Work ideas', exact: true }).click();
  await page.getByRole('button', { name: 'End & switch' }).click();
  await expect.poll(() => ends.some((url) => url.endsWith('/sess-1/end'))).toBe(true);
  await expect(page.locator('.orb-button')).toBeVisible();
});

test('starting a new conversation mid-call requires the same confirmation', async ({ page }) => {
  const mocked = await mockHistory(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await mockSessions(page);

  await gotoHistory(page);
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
  await page.locator('.orb-button').click();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'New conversation', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'End the current voice session?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(mocked.created()).toBe(0);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'New conversation', exact: true }).click();
  await page.getByRole('button', { name: 'End & start' }).click();
  await expect.poll(() => mocked.created()).toBe(1);
  await expect(page.locator('.orb-button')).toBeVisible();
});

test('delete confirms inline, removes the chat and refreshes the list', async ({ page }) => {
  const mocked = await mockHistory(page);
  await gotoHistory(page);
  await page.getByRole('button', { name: 'Conversation actions for Trip to Paris' }).click();
  const menu = page.getByRole('menu');

  await expect(menu.getByRole('menuitem', { name: 'Rename' })).toBeDisabled();
  await expect(menu).toContainText('Rename needs backend support.');
  const deleteItem = menu.getByRole('menuitem', { name: 'Delete' });
  await expect(deleteItem).toBeEnabled();

  await deleteItem.click();
  await expect(menu.getByText('Delete this chat?')).toBeVisible();
  await expect(menu).toContainText(
    'It and its temporary memories are erased permanently. Permanent memories are kept.',
  );

  await menu.getByRole('button', { name: 'Delete this chat' }).click();

  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Work ideas', exact: true })).not.toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(mocked.deleted()).toEqual(['c1']);
  await expect.poll(() => mocked.listCalls()).toBe(2);
  // Deleting unmounts the focused menu; focus must land on the next row, never <body>.
  await expect(page.getByRole('button', { name: 'ميزانية Tripoli', exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
});

test('the delete confirmation cannot be dismissed while the delete is in flight', async ({ page }) => {
  await mockHistory(page);
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route(/\/api\/v1\/conversations\/[^/?]+(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'DELETE') {
      await route.fallback();
      return;
    }
    await gate;
    // Hand over to the harness handler so it records the delete; the list then really loses the row.
    await route.fallback();
  });
  try {
    await gotoHistory(page);
    await page.getByRole('button', { name: 'Conversation actions for Trip to Paris' }).click();
    const menu = page.getByRole('menu');
    await menu.getByRole('menuitem', { name: 'Delete' }).click();
    await menu.getByRole('button', { name: 'Delete this chat' }).click();
    await expect(menu.getByText('Deleting…')).toBeVisible();

    // With focus inside the sidebar the shell would close this dock on Escape: the menu must consume it.
    const search = page.getByRole('searchbox', { name: 'Search conversations' });
    await search.focus();
    await page.keyboard.press('Escape');
    await expect(search).toHaveCount(1);
    await search.click();
    await expect(menu).toBeVisible();
    await expect(menu.getByText('Deleting…')).toBeVisible();

    release?.();
    await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toHaveCount(0);
  } finally {
    release?.();
  }
});

test('a refused or failed delete explains why and keeps the row', async ({ page }) => {
  await mockHistory(page);
  await page.route(/\/api\/v1\/conversations\/[^/?]+(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'DELETE') {
      await route.fallback();
      return;
    }
    const id = new URL(route.request().url()).pathname.split('/').pop();
    if (id === 'c1') {
      await route.fulfill({ status: 409, json: { detail: { code: 'CONVERSATION_ACTIVE' } } });
    } else {
      await route.fulfill({ status: 500, json: { detail: { code: 'INTERNAL_ERROR' } } });
    }
  });
  await gotoHistory(page);

  await page.getByRole('button', { name: 'Conversation actions for Trip to Paris' }).click();
  const menu = page.getByRole('menu');
  await menu.getByRole('menuitem', { name: 'Delete' }).click();
  await menu.getByRole('button', { name: 'Delete this chat' }).click();
  await expect(menu.getByText('End the call before deleting this chat')).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await expect(page.getByRole('button', { name: 'Trip to Paris', exact: true })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await page.getByRole('button', { name: 'Conversation actions for Work ideas' }).click();
  await menu.getByRole('menuitem', { name: 'Delete' }).click();
  await menu.getByRole('button', { name: 'Delete this chat' }).click();
  await expect(menu.getByText('Could not delete this chat. Try again.')).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await expect(page.getByRole('button', { name: 'Work ideas', exact: true })).toBeVisible();
});
