import { expect, test } from '@playwright/test';
import { allowMicrophone, installFakeRuntime, mockSessions, simulate, startCall } from './support/callStateHarness';

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('a denied unmute returns to listening after a successful microphone retry', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await page.getByRole('button', { name: 'Mute', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unmute microphone', exact: true })).toBeVisible();

  await page.evaluate(() => {
    if (window.__callState) window.__callState.state.failMicrophone = true;
  });
  await page.getByRole('button', { name: 'Unmute microphone', exact: true }).click();

  const notice = page.locator('.voice-stage [data-code="MICROPHONE_PERMISSION_DENIED"]');
  await expect(notice).toContainText('Microphone access is blocked');
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'listening');
  await expect(page.locator('.connected-pill')).toHaveCount(0);

  await page.getByRole('button', { name: 'Retry microphone', exact: true }).click();
  await expect(notice).toHaveCount(0);
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'listening');
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  await expect(page.locator('.connected-pill')).toContainText('Connected');
  expect(ends).toHaveLength(0);
});

test('a recoverable error on a live call keeps the normal actions and never reconnects', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'error', code: 'TRANSCRIPT_UNAVAILABLE' });
  const notice = page.locator('.voice-stage [data-code="TRANSCRIPT_UNAVAILABLE"]');
  await expect(notice).toContainText('The transcript could not be read');
  await expect(notice).not.toHaveAttribute('role', 'status');
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'listening');
  await expect(page.getByRole('button', { name: 'Mute', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'End call', exact: true })).toBeVisible();

  await simulate(page, { type: 'status', source: 'agent', text: 'speaking' });
  await expect(page.getByRole('button', { name: 'Interrupt', exact: true })).toBeVisible();
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'speaking');

  await page.locator('.orb-button').click();
  await expect.poll(() => page.evaluate(() => window.__callState?.state.interrupts)).toBe(1);
  expect(ends).toHaveLength(0);
  await expect(page.locator('[data-code="CONNECTION_LOST"]')).toHaveCount(0);
});

test('a lost connection shows Offline and never the Connected pill', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'status', source: 'transport', text: 'disconnected' });
  await expect(page.locator('.status-line')).toContainText('Offline');
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'disconnected');
  await expect(page.locator('.connected-pill')).toHaveCount(0);
  const lost = page.locator('.voice-stage [data-code="CONNECTION_LOST"]');
  await expect(lost).toContainText('Connection lost');
  await expect(lost).not.toHaveAttribute('role', 'status');
  await expect(page.locator('p.sr-only[aria-live="polite"]')).toHaveText('Offline');
});

test('the stage status lines announce nothing themselves', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await expect(page.locator('.voice-stage [role="status"]')).toHaveCount(0);
  await simulate(page, { type: 'status', source: 'agent', text: 'thinking' });
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'thinking');
  await expect(page.locator('.voice-stage [role="status"]')).toHaveCount(0);
});

test('blocked audio playback explains the Enable audio button', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'audio-blocked', code: 'AUDIO_PLAYBACK_BLOCKED' });
  const enable = page.getByRole('button', { name: 'Enable audio', exact: true });
  await expect(enable).toBeVisible();
  const notice = page.locator('.stage-notice[role="status"]');
  await expect(notice).toContainText('The browser blocked audio playback. Select “Enable audio” to continue.');
});

test('a fatal error is not replaced by a recoverable runtime error', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'error', code: 'SESSION_RELEASE_FAILED' });
  await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'error');
  await expect(page.locator('.voice-stage [data-code="SESSION_RELEASE_FAILED"]')).toBeVisible();

  for (const code of ['MICROPHONE_UNAVAILABLE', 'DAILY_BUDGET_EXHAUSTED', 'CONVERSATION_REQUIRED']) {
    await simulate(page, { type: 'error', code });
    await expect(page.locator('.voice-stage [data-code="SESSION_RELEASE_FAILED"]')).toBeVisible();
    await expect(page.locator(`.voice-stage [data-code="${code}"]`)).toHaveCount(0);
    await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', 'error');
  }
});

test('a healthy agent status clears a recoverable agent error', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await simulate(page, { type: 'error', code: 'TRANSCRIPT_UNAVAILABLE' });
  await expect(page.locator('.voice-stage [data-code="TRANSCRIPT_UNAVAILABLE"]')).toBeVisible();

  await simulate(page, { type: 'status', source: 'agent', text: 'idle' });
  await expect(page.locator('.voice-stage [data-code="TRANSCRIPT_UNAVAILABLE"]')).toHaveCount(0);
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
});

test('the page live region announces the voice phase', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  const liveRegion = page.locator('p.sr-only[aria-live="polite"]');
  await expect(liveRegion).toHaveText('Listening…');
  await simulate(page, { type: 'status', source: 'agent', text: 'thinking' });
  await expect(liveRegion).toHaveText('Thinking…');
});

test('reconnecting shows Reconnecting and drops the Connected pill', async ({ page }) => {
  await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await expect(page.locator('.connected-pill')).toContainText('Connected');
  await simulate(page, { type: 'status', source: 'transport', text: 'reconnecting' });
  await expect(page.locator('.status-line')).toContainText('Reconnecting…');
  await expect(page.locator('.connected-pill')).toHaveCount(0);
  await expect(page.locator('p.sr-only[aria-live="polite"]')).toHaveText('Reconnecting…');

  await simulate(page, { type: 'status', source: 'transport', text: 'connected' });
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  await expect(page.locator('.connected-pill')).toContainText('Connected');
});

test('a generic microphone failure keeps the Retry microphone button', async ({ page }) => {
  const { ends } = await mockSessions(page);
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await startCall(page);

  await page.getByRole('button', { name: 'Mute', exact: true }).click();
  await page.evaluate(() => {
    if (window.__callState) window.__callState.state.genericMicrophoneFailure = true;
  });
  await page.getByRole('button', { name: 'Unmute microphone', exact: true }).click();

  await expect(page.locator('.voice-stage [data-code="MICROPHONE_UNAVAILABLE"]')).toContainText(
    'Microphone unavailable',
  );
  await expect(page.getByRole('button', { name: 'Retry microphone', exact: true })).toBeVisible();
  expect(ends).toHaveLength(0);
});

test('Arabic copy smoke for reconnecting and offline', async ({ page }) => {
  await page.goto('/');
  const copy = await page.evaluate(async () => {
    const path = '/src/lib/chat/words.ts';
    const mod = (await import(/* @vite-ignore */ path)) as {
      wordsFor(arabic: boolean): { reconnecting: string; offline: string; audioBlocked: string };
    };
    return mod.wordsFor(true);
  });
  expect(copy.reconnecting).toBe('جارٍ إعادة الاتصال…');
  expect(copy.offline).toBe('غير متصل');
  expect(copy.audioBlocked).toBe('منع المتصفح تشغيل الصوت. اختر «تشغيل الصوت» للمتابعة.');
});
