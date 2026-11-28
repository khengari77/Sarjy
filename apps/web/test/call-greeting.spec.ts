import { expect, test } from '@playwright/test';
import { allowMicrophone, installFakeRuntime, mockSessions } from './support/callStateHarness';
import { gotoHistory } from './support/historyHarness';

declare global {
  interface Window {
    __greetingAudio?: HTMLAudioElement;
    __endGreeting?: () => void;
  }
}

async function stubGreetingAudio(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    HTMLAudioElement.prototype.play = function () {
      window.__greetingAudio = this;
      return Promise.resolve();
    };
    window.__endGreeting = () => window.__greetingAudio?.dispatchEvent(new Event('ended'));
  });
}

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('the greeting captions first and the microphone waits for it to end', async ({ page }) => {
  await mockSessions(page, {
    intro: { kind: 'short', text: 'Hi again, what can I help with?', asset: 'aura-2-thalia-en/short.mp3' },
  });
  await allowMicrophone(page);
  await installFakeRuntime(page);
  await stubGreetingAudio(page);

  // The call is live but still connecting to the UI while the greeting plays.
  await gotoHistory(page);
  await page.locator('.orb-button').click();

  const caption = page.locator('.caption-bubble');
  await expect(caption).toContainText('Hi again, what can I help with?');
  await expect(page.locator('.voice-stage [role="status"]')).toHaveText('Playing the greeting.');
  await expect(page.locator('.status-line')).toHaveAttribute('data-phase', 'listening');
  await expect.poll(() => page.evaluate(() => window.__callState?.state.microphoneStarts)).toBe(0);

  await page.evaluate(() => window.__endGreeting?.());

  await expect(caption).not.toContainText('Hi again, what can I help with?');
  await expect(page.locator('.voice-stage [role="status"]')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => window.__callState?.state.microphoneStarts)).toBe(1);
  await expect(page.getByRole('button', { name: 'Mute', exact: true })).toBeVisible();
});
