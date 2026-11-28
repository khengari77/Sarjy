import { expect, test, type Page } from '@playwright/test';
import { allowMicrophone, installFakeRuntime, mockSessions, simulate, startCall } from './support/callStateHarness';
import { gotoHistory, mockHistory } from './support/historyHarness';

function callCommand(action: string, spoken: string, requestId = `command:${action}`) {
  return {
    request_id: requestId,
    kind: 'executed',
    action,
    settings: null,
    navigation: null,
    theme: null,
    changed: true,
    spoken,
    detail: null,
  };
}

function emitCommand(page: Page, action: string, spoken: string, requestId?: string) {
  return simulate(page, { type: 'command', command: callCommand(action, spoken, requestId) });
}

function runtimeState(page: Page) {
  return page.evaluate(() => window.__callState?.state);
}

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('a typed EndCall with no live call replies and does nothing', async ({ page }) => {
  await mockHistory(page);
  await installFakeRuntime(page);
  await gotoHistory(page);

  await emitCommand(page, 'EndCall', 'Ending the call.');

  const status = page.locator('[data-command-kind]');
  await expect(status).toHaveAttribute('data-command-kind', 'executed');
  await expect(status).toHaveText('There is no call to end.');
  expect(await page.locator('.status-line').getAttribute('data-phase')).not.toBe('connecting');
});

test('EndCall waits for the spoken reply before ending the live call', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await mockHistory(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await emitCommand(page, 'EndCall', 'Ending the call.');
  await simulate(page, { type: 'status', source: 'agent', text: 'speaking' });
  await expect(page.locator('[data-command-kind]')).toHaveText('Ending the call.');
  expect(ends).toHaveLength(0);

  await simulate(page, { type: 'status', source: 'agent', text: 'listening' });
  await expect.poll(() => ends.length).toBe(1);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toHaveCount(0);
});

test('a second MuteMicrophone does not unmute the microphone', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await mockHistory(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);
  expect((await runtimeState(page))?.mutes).toBe(0);

  await emitCommand(page, 'MuteMicrophone', 'Muted. Tap the microphone to unmute.');
  await expect(page.getByRole('button', { name: 'Unmute microphone', exact: true })).toBeVisible();
  await expect.poll(async () => (await runtimeState(page))?.mutes).toBe(1);

  await emitCommand(page, 'MuteMicrophone', 'Muted. Tap the microphone to unmute.', 'command:mute-2');

  const status = page.locator('[data-command-kind]');
  await expect(status).toHaveText('The microphone is already muted.');
  expect((await runtimeState(page))?.mutes).toBe(1);
  expect((await runtimeState(page))?.microphoneStarts).toBe(1);
  expect(ends).toHaveLength(0);
});

test('NewConversation ends the live call, creates a chat, and reconnects', async ({ page }) => {
  const { sessions, ends } = await mockSessions(page);
  const history = await mockHistory(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);
  expect(sessions).toHaveLength(1);

  await emitCommand(page, 'NewConversation', 'Starting a new conversation.');
  await simulate(page, { type: 'status', source: 'agent', text: 'speaking' });
  await expect(page.locator('[data-command-kind]')).toHaveText('Starting a new conversation.');
  expect(ends).toHaveLength(0);

  await simulate(page, { type: 'status', source: 'agent', text: 'listening' });

  await expect.poll(() => ends.length).toBe(1);
  await expect.poll(() => history.created()).toBe(1);
  await expect.poll(() => sessions.length).toBe(2);
  await expect.poll(async () => (await runtimeState(page))?.connects).toBe(2);
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();
});
