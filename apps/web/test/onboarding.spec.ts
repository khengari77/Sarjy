import { test, expect, type Page } from '@playwright/test';

type Stage =
  'greeting' | 'preferred_name' | 'voice_choice' | 'capability_intro' | 'completed' | 'skipped' | 'suspended';

interface MockOnboarding {
  stage: Stage;
  preferred_name: string | null;
  suspended_from: Stage | null;
  revision: number;
}

const GREETING = "Hey, I'm Sarjy. What should I call you?";

async function mockOnboarding(page: Page, initial: Partial<MockOnboarding> = {}) {
  const state: MockOnboarding = {
    stage: 'greeting',
    preferred_name: null,
    suspended_from: null,
    revision: 1,
    ...initial,
  };
  const progress = () => ({
    schema_version: 1,
    revision: state.revision,
    stage: state.stage,
    preferred_name: state.preferred_name,
    selected_voice_id: null,
    selected_pace: null,
    intro_played_at: state.stage === 'greeting' ? null : '2026-11-14T09:00:00+00:00',
    started_at: '2026-11-14T09:00:00+00:00',
    completed_at: null,
    suspended_from: state.suspended_from,
    last_event_id: null,
    greeting: state.stage === 'greeting' ? GREETING : null,
    voice_application: null,
    capabilities: { speaking_pace: false, weather: true, memory: true, wordle: true, chat: true },
  });
  await page.route(/\/api\/v1\/onboarding$/, (route) => route.fulfill({ json: progress() }));
  await page.route(/\/api\/v1\/onboarding\/events$/, async (route) => {
    const body = route.request().postDataJSON() as { type: string; name?: string };
    state.revision += 1;
    if (body.type === 'greeting_played' && state.stage === 'greeting') state.stage = 'preferred_name';
    if (body.type === 'submit_name') {
      state.preferred_name = body.name?.trim() ?? null;
      state.stage = 'voice_choice';
    }
    if (body.type === 'choose_voice') state.stage = 'capability_intro';
    if (body.type === 'acknowledge_capabilities') state.stage = 'completed';
    if (body.type === 'skip') state.stage = 'skipped';
    if (body.type === 'suspend') {
      state.suspended_from = state.stage;
      state.stage = 'suspended';
    }
    if (body.type === 'resume') {
      state.stage = state.suspended_from ?? 'capability_intro';
      state.suspended_from = null;
    }
    await route.fulfill({ json: progress() });
  });
  return state;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('greets once, saves a name, shows confirmation and skips without trapping chat', async ({ page }) => {
  await mockOnboarding(page);
  await page.goto('/');
  const greeting = page.getByText(GREETING);
  await expect(greeting).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Skip setup' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue by typing' }).click();
  await page.getByLabel('What should I call you?').fill('Sam');
  await page.getByRole('button', { name: 'Save name' }).click();
  await expect(page.getByText('Call you: Sam')).toBeVisible();
  await page.getByRole('button', { name: 'Skip setup' }).click();
  await expect(greeting).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Skip setup' })).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: 'Write a message…' })).toBeEditable();
});

test('an unrelated message suspends setup and leaves a Continue setup affordance', async ({ page }) => {
  await mockOnboarding(page, { stage: 'preferred_name', revision: 2 });
  await page.goto('/');
  await page.getByRole('textbox', { name: 'Write a message…' }).fill("What's the weather in Tripoli?");
  await page.getByRole('button', { name: /^Send/ }).click();
  await expect(page.getByRole('button', { name: 'Continue setup' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue setup' }).click();
  await expect(page.getByLabel('What should I call you?')).toBeVisible();
});

test('refresh resumes the same stage without replaying the greeting', async ({ page }) => {
  await mockOnboarding(page, { stage: 'voice_choice', preferred_name: 'Sam', revision: 4 });
  await page.goto('/');
  await expect(page.getByText('Call you: Sam')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Call you: Sam')).toBeVisible();
  await expect(page.getByText(GREETING)).toHaveCount(0);
});

test('a denied microphone leaves typed onboarding usable', async ({ page }) => {
  await mockOnboarding(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          throw new DOMException('blocked', 'NotAllowedError');
        },
      },
    });
  });
  await page.goto('/');
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static');
  await page.locator('.orb-button').click();
  await expect(page.getByText(/Microphone access is blocked/i)).toBeVisible();
  await page.getByRole('button', { name: 'Continue by typing' }).click();
  await expect(page.getByLabel('What should I call you?')).toBeVisible();
});

test('the memory line opens the real explorer and writes no memory', async ({ page }) => {
  const memoryWrites: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/memories') && request.method() !== 'GET') memoryWrites.push(request.url());
  });
  await mockOnboarding(page, { stage: 'capability_intro', preferred_name: 'Sam', revision: 5 });
  await page.goto('/');
  await expect(page.getByText("Play today's Wordle.")).toBeVisible();
  const link = page.getByRole('link', { name: 'Review or forget it in Memory' });
  await expect(link).toHaveAttribute('href', '#memory-explorer');
  await link.click();
  await expect(page.locator('#memory-explorer')).toBeFocused();
  await expect(page.locator('aside[aria-label="Memory and settings"]')).toHaveAttribute('data-open', 'true');
  expect(memoryWrites).toEqual([]);
});
