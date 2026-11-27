import { expect, test } from '@playwright/test';
import { boot, command, emit, snapshot, withPage, VOICE_SETTINGS } from './support/voiceCommandHarness';

test('GUI captions action and a voice result converge through the one settings path', async ({ browser }) => {
  let guiState: Awaited<ReturnType<typeof snapshot>> | undefined;
  let voiceState: Awaited<ReturnType<typeof snapshot>> | undefined;

  await withPage(browser, async (page, backend) => {
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await page.locator('#shell-right').getByRole('tab', { name: 'Settings' }).click();
    await page.getByRole('checkbox', { name: 'Show captions' }).uncheck();
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    guiState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(1);
    expect(backend.settingsWrites[0]).toMatchObject({
      request_id: 'captions:0:off',
      action: { kind: 'SetCaptionsVisible', visible: false },
    });
    expect(backend.actionWrites).toHaveLength(0);
    await expect(page.locator('.caption-bubble:not(.quiet)')).toHaveCount(0);
  });

  await withPage(browser, async (page, backend) => {
    await emit(page, { type: 'command', command: command() });
    await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', 'executed');
    voiceState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(0);
    expect(backend.actionWrites).toHaveLength(0);
    await expect(page.locator('.caption-bubble:not(.quiet)')).toHaveCount(0);
  });

  expect(voiceState).toEqual(guiState);
  expect(voiceState).toMatchObject({ trace: 'SetCaptionsVisible', revision: '1', captions: 'false' });
});

test('GUI picker and voice selection converge on the same approved-voice state', async ({ browser }) => {
  let guiState: Awaited<ReturnType<typeof snapshot>> | undefined;
  let voiceState: Awaited<ReturnType<typeof snapshot>> | undefined;

  await withPage(browser, async (page, backend) => {
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await page.getByRole('button', { name: 'Load voices / Search', exact: true }).click();
    await page.getByLabel('Choose voice').selectOption('approved');
    await expect(page.locator('[data-settings-revision]')).toHaveAttribute('data-settings-revision', '1');
    guiState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(1);
    expect(backend.settingsWrites[0]).toMatchObject({
      request_id: 'voice:0:approved',
      action: { kind: 'SelectApprovedVoice', voice_id: 'approved' },
    });
    expect(backend.actionWrites).toHaveLength(0);
    await expect(page.getByLabel('Choose voice')).toHaveValue('approved');
  });

  await withPage(browser, async (page, backend) => {
    await emit(page, {
      type: 'command',
      command: command({
        action: 'SelectApprovedVoice',
        settings: VOICE_SETTINGS,
        spoken: 'Voice set to Approved Narrator for the next call.',
      }),
    });
    await expect(page.locator('[data-settings-revision]')).toHaveAttribute('data-settings-revision', '1');
    voiceState = await snapshot(page);
    expect(backend.settingsWrites).toHaveLength(0);
    expect(backend.actionWrites).toHaveLength(0);
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await expect(page.getByLabel('Choose voice')).toHaveValue('approved');
  });

  expect(voiceState).toEqual(guiState);
  expect(voiceState).toMatchObject({ trace: 'SelectApprovedVoice', revision: '1', voice: 'approved' });
});

test('a partial transcript dispatches nothing and a final transcript is not parsed in the browser', async ({
  page,
}) => {
  await boot(page);

  await emit(page, { type: 'transcript', text: 'Hide captions', final: false, segmentId: 'seg-1', speaker: 'user' });
  await emit(page, { type: 'transcript', text: 'Hide captions.', final: true, segmentId: 'seg-1', speaker: 'user' });

  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', '');
  await expect(page.locator('[data-command-kind]')).toHaveAttribute('data-command-kind', '');
  expect(await snapshot(page)).toMatchObject({ captions: 'true' });
});

test('a duplicate finalized command result applies once', async ({ page }) => {
  await boot(page);
  await emit(page, { type: 'command', command: command() });
  await emit(page, { type: 'command', command: command() });
  await expect(page.locator('[data-command-trace]')).toHaveAttribute('data-command-trace', 'SetCaptionsVisible');
  expect(await snapshot(page)).toMatchObject({ revision: '1', captions: 'false' });
});

test('"open the memory explorer" shows the Memories panel, not the voice picker', async ({ browser }) => {
  await withPage(browser, async (page) => {
    await emit(page, {
      type: 'command',
      command: command({
        action: 'OpenMemoryExplorer',
        settings: null,
        navigation: 'memory_explorer',
        spoken: 'Opening the memory explorer.',
      }),
    });
    await expect(page.locator('#shell-right')).toHaveAttribute('data-open', 'true');
    await expect(page.locator('#drawer-tab-memories')).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('#drawer-tab-controls')).toHaveAttribute('aria-selected', 'false');
    await expect(page.locator('#drawer-panel-memories')).toBeVisible();
    await expect(page.locator('#drawer-panel-controls')).toBeHidden();
  });
});

test('voice navigation opens the same panel the GUI control opens', async ({ browser }) => {
  const guiOpen: string[] = [];
  const voiceOpen: string[] = [];

  await withPage(browser, async (page) => {
    await expect(page.locator('#shell-right')).toHaveAttribute('data-open', 'false');
    await page.getByRole('button', { name: 'Show memory and settings', exact: true }).click();
    await expect(page.locator('#shell-right')).toHaveAttribute('data-open', 'true');
    guiOpen.push((await page.locator('#shell-right').getAttribute('data-open')) ?? '');
  });

  await withPage(browser, async (page) => {
    await expect(page.locator('#shell-right')).toHaveAttribute('data-open', 'false');
    await emit(page, {
      type: 'command',
      command: command({
        action: 'OpenMemoryExplorer',
        settings: null,
        navigation: 'memory_explorer',
        spoken: 'Opening the memory explorer.',
      }),
    });
    await expect(page.locator('#shell-right')).toHaveAttribute('data-open', 'true');
    voiceOpen.push((await page.locator('#shell-right').getAttribute('data-open')) ?? '');
  });

  expect(voiceOpen).toEqual(guiOpen);
});
