import { expect, test, type Page } from '@playwright/test';
import { evidenceSha, mockApi, waitForHydration, writeEvidenceJson } from './support/evidence';
import {
  analyseSamples,
  installProfiler,
  openCdpSession,
  readSnapshot,
  type ResourceReport,
  type ResourceSnapshot,
} from './support/resourceProfile';

const ENABLED = process.env.SARJY_RESOURCE_PROFILE === '1';
const DURATION_SECONDS = Number(process.env.SARJY_RESOURCE_PROFILE_SECONDS ?? 300);
const SAMPLE_INTERVAL_SECONDS = Number(process.env.SARJY_RESOURCE_PROFILE_INTERVAL ?? 15);
const MODE: ResourceReport['mode'] = process.env.SARJY_RESOURCE_PROFILE_STATE === 'listening' ? 'listening' : 'idle';

async function installFakeListeningRuntime(page: Page): Promise<void> {
  await page.addInitScript(() => {
    class FakeRuntime {
      __epoch = 1;
      handlers = new Set<(event: unknown) => void>();
      get epoch() {
        return this.__epoch;
      }
      subscribe(handler: (event: unknown) => void) {
        this.handlers.add(handler);
        return () => this.handlers.delete(handler);
      }
      emit(event: Record<string, unknown>) {
        for (const handler of [...this.handlers]) handler({ ...event, epoch: this.__epoch });
      }
      async connect() {
        this.emit({ type: 'status', source: 'transport', text: 'connected' });
        this.emit({ type: 'status', source: 'agent', text: 'listening' });
      }
      async disconnect() {
        this.__epoch += 1;
      }
      async cancelConnect() {}
      async startMicrophone() {}
      async stopMicrophone() {}
      async sendText() {}
      async resumeAudio() {}
      async interrupt() {}
      microphoneStream() {
        return null;
      }
      remoteAudioLevel() {
        return 0;
      }
    }
    (window as unknown as { __SARJY_VOICE_RUNTIME_FACTORY__: () => unknown }).__SARJY_VOICE_RUNTIME_FACTORY__ = () =>
      new FakeRuntime();
  });
  await page.route('**/api/v1/voice/sessions', (route) =>
    route.fulfill({
      json: {
        id: 'sess-resource-profile',
        url: 'ws://fake.invalid',
        token: 'fake',
        expires_at: new Date(Date.now() + 600_000).toISOString(),
      },
    }),
  );
}

test(`resource profile: ${DURATION_SECONDS}s ${MODE}`, async ({ page, browser }) => {
  test.skip(
    !ENABLED,
    'Opt-in 5-minute stability run. Use pnpm --dir apps/web profile:resources (SARJY_RESOURCE_PROFILE=1).',
  );
  test.setTimeout((DURATION_SECONDS + 120) * 1000);

  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page);
  if (MODE === 'listening') await installFakeListeningRuntime(page);
  await installProfiler(page);

  await page.goto('/');
  await waitForHydration(page);
  if (MODE === 'listening') {
    await page.getByRole('button', { name: 'Start call', exact: true }).click();
    await expect(page.locator('.status-line')).toContainText('Listening');
  }

  const session = await openCdpSession(page);
  const samples: ResourceSnapshot[] = [];
  const startedAt = Date.now();
  for (let elapsed = 0; elapsed <= DURATION_SECONDS; elapsed += SAMPLE_INTERVAL_SECONDS) {
    samples.push(await readSnapshot(page, session, Math.round((Date.now() - startedAt) / 1000)));
    if (elapsed < DURATION_SECONDS) await page.waitForTimeout(SAMPLE_INTERVAL_SECONDS * 1000);
  }
  await session.detach();

  const analysis = analyseSamples(samples);
  const environment = await page.evaluate(() => ({
    userAgent: navigator.userAgent,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemoryGb: (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? null,
    viewport: { width: window.innerWidth, height: window.innerHeight },
  }));

  const report: ResourceReport = {
    sha: evidenceSha(),
    mode: MODE,
    durationSeconds: DURATION_SECONDS,
    sampleIntervalSeconds: SAMPLE_INTERVAL_SECONDS,
    environment: { ...environment, browserVersion: browser.version(), headless: true },
    samples,
    ...analysis,
    limitations: [
      'Headless Chromium measurement; real-provider audio/GPU traffic is not exercised.',
      'WebGL count is live canvas contexts created by the app, not individual GPU buffers.',
      'Idle/listening uses the app runtime factory and DEV fixtures where noted.',
    ],
  };
  await writeEvidenceJson('resource-profile.json', report);
  expect(analysis.violations, `resource growth exceeded tolerance: ${analysis.violations.join('; ')}`).toEqual([]);
  expect(samples.length).toBeGreaterThanOrEqual(2);
});
