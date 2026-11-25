import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  fetchSettings: vi.fn(),
  saveCaptionSelection: vi.fn(),
  saveMemoryMode: vi.fn(),
  saveSpeakingSpeed: vi.fn(),
}));

vi.mock('../chat/session', () => mocks);

import { createCommandSession } from './commandSession.svelte';

const snapshot = (memory_mode: 'off' | 'ask' | 'auto', revision: number) => ({
  speaking_speed: 1,
  captions_visible: true,
  approved_voice_id: null,
  response_preference: 'automatic',
  memory_mode,
  revision,
});

describe('memory mode control', () => {
  beforeEach(() => {
    mocks.fetchSettings.mockReset();
    mocks.saveCaptionSelection.mockReset();
    mocks.saveMemoryMode.mockReset();
    mocks.saveSpeakingSpeed.mockReset();
  });

  it('writes the mode through the settings path and applies the server snapshot', async () => {
    mocks.saveMemoryMode.mockResolvedValue({
      kind: 'SetMemoryMode',
      settings: snapshot('off', 1),
      changed: true,
    });
    const session = createCommandSession();

    expect(await session.setMemoryMode('off')).toBe(true);

    expect(mocks.saveMemoryMode).toHaveBeenCalledWith('off', 0);
    expect(session.state.settings.memory_mode).toBe('off');
    expect(session.state.settings.revision).toBe(1);
    expect(session.state.last).toMatchObject({ kind: 'executed', action: 'SetMemoryMode', changed: true });
    expect(session.state.trace).toEqual(['SetMemoryMode']);
    expect(session.memoryError).toBe('');
  });

  it('a rejected write leaves the previous mode and revision in place', async () => {
    mocks.saveMemoryMode.mockRejectedValue(new Error('SETTINGS_REVISION_CONFLICT'));
    mocks.fetchSettings.mockRejectedValue(new Error('SETTINGS_UNAVAILABLE'));
    const session = createCommandSession();

    expect(await session.setMemoryMode('ask')).toBe(false);

    expect(session.state.settings.memory_mode).toBe('auto');
    expect(session.state.settings.revision).toBe(0);
    expect(session.state.last).toMatchObject({ kind: 'failed', action: 'SetMemoryMode' });
    expect(session.state.trace).toEqual([]);
    expect(session.memoryError).toBe("Couldn't change memory mode. Nothing was changed.");
  });

  it('refetches settings and retries once after a revision conflict', async () => {
    mocks.saveMemoryMode
      .mockRejectedValueOnce(new Error('SETTINGS_REVISION_CONFLICT'))
      .mockResolvedValueOnce({ kind: 'SetMemoryMode', settings: snapshot('off', 6), changed: true });
    mocks.fetchSettings.mockResolvedValueOnce(snapshot('ask', 5));
    const session = createCommandSession();

    expect(await session.setMemoryMode('off')).toBe(true);

    expect(mocks.fetchSettings).toHaveBeenCalledTimes(1);
    expect(mocks.saveMemoryMode.mock.calls).toEqual([
      ['off', 0],
      ['off', 5],
    ]);
    expect(session.state.settings.memory_mode).toBe('off');
    expect(session.state.settings.revision).toBe(6);
    expect(session.memoryError).toBe('');
    expect(session.memorySaving).toBe(false);
  });

  it('ignores a second call while a save is already in flight', async () => {
    let resolveSave!: (value: unknown) => void;
    mocks.saveMemoryMode.mockReturnValue(new Promise((resolve) => (resolveSave = resolve)));
    const session = createCommandSession();

    const first = session.setMemoryMode('off');
    expect(session.memorySaving).toBe(true);
    expect(await session.setMemoryMode('ask')).toBe(false);
    expect(mocks.saveMemoryMode).toHaveBeenCalledTimes(1);

    resolveSave({ kind: 'SetMemoryMode', settings: snapshot('off', 1), changed: true });
    expect(await first).toBe(true);
    expect(session.state.settings.memory_mode).toBe('off');
    expect(session.memorySaving).toBe(false);
  });

  it('a final failure sets the error and leaves the previous mode', async () => {
    mocks.saveMemoryMode.mockRejectedValue(new Error('SETTINGS_REVISION_CONFLICT'));
    mocks.fetchSettings.mockResolvedValue(snapshot('auto', 5));
    const session = createCommandSession();

    expect(await session.setMemoryMode('off')).toBe(false);

    expect(mocks.fetchSettings).toHaveBeenCalledTimes(1);
    expect(mocks.saveMemoryMode.mock.calls).toEqual([
      ['off', 0],
      ['off', 5],
    ]);
    expect(session.memoryError).toBe('Settings changed in another window. Try again.');
    expect(session.state.settings.memory_mode).toBe('auto');
    expect(session.state.last).toMatchObject({ kind: 'failed', action: 'SetMemoryMode' });
    expect(session.memorySaving).toBe(false);
  });
});
