import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  fetchSettings: vi.fn(),
  saveCaptionSelection: vi.fn(),
  saveMemoryMode: vi.fn(),
  saveSpeakingSpeed: vi.fn(),
  undoChange: vi.fn(),
}));

vi.mock('../chat/session', () => mocks);

import { createCommandSession } from './commandSession.svelte';
import type { CommandResult } from './commandStore';

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
    mocks.undoChange.mockReset();
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

function memoryChange(id = 'change-1', label = 'Remembered: prefers oat milk.'): CommandResult {
  return {
    request_id: `memauto:${id}`,
    kind: 'executed',
    action: 'MemoryChange',
    settings: null,
    navigation: null,
    theme: null,
    changed: true,
    spoken: label,
    detail: null,
    change: { id, kind: 'memory_saved', label, undoable: true, memory_id: 'memory-1' },
  };
}

const undoResult = {
  id: 'change-1',
  kind: 'memory_saved' as const,
  label: 'Remembered: prefers oat milk.',
  undoable: false,
  memory_id: 'memory-1',
  changed: true,
};

describe('memory change undo', () => {
  beforeEach(() => {
    mocks.fetchSettings.mockReset();
    mocks.saveCaptionSelection.mockReset();
    mocks.saveMemoryMode.mockReset();
    mocks.saveSpeakingSpeed.mockReset();
    mocks.undoChange.mockReset();
  });

  it('undoes through the change endpoint, refreshes memories and dismisses the toast', async () => {
    mocks.undoChange.mockResolvedValue(undoResult);
    const refresh = vi.fn();
    const session = createCommandSession(undefined, undefined, { onMemoryChange: refresh });
    session.apply(memoryChange());
    const revision = session.memoryRevision;

    expect(await session.undoChange('change-1')).toBe(true);

    expect(mocks.undoChange).toHaveBeenCalledWith('change-1');
    expect(session.change).toBeNull();
    expect(session.memoryRevision).toBe(revision + 1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(session.changeStatus).toBe('Undone.');
    expect(session.changeError).toBe('');
  });

  it('keeps the toast and shows the superseded message on 409', async () => {
    mocks.undoChange.mockRejectedValue(new Error('CHANGE_SUPERSEDED'));
    const refresh = vi.fn();
    const session = createCommandSession(undefined, undefined, { onMemoryChange: refresh });
    session.apply(memoryChange());
    const revision = session.memoryRevision;

    expect(await session.undoChange('change-1')).toBe(false);

    expect(session.change?.id).toBe('change-1');
    expect(session.changeError).toBe('That memory changed since, so I left it alone.');
    expect(session.changeStatus).toBe('');
    expect(session.memoryRevision).toBe(revision);
    expect(refresh).not.toHaveBeenCalled();
  });

  it('keeps the toast and shows the missing-change message on 404', async () => {
    mocks.undoChange.mockRejectedValue(new Error('CHANGE_NOT_FOUND'));
    const session = createCommandSession();
    session.apply(memoryChange());

    expect(await session.undoChange('change-1')).toBe(false);

    expect(session.change?.id).toBe('change-1');
    expect(session.changeError).toBe('That change is no longer available.');
  });

  it('ignores a second undo while one is in flight', async () => {
    let resolveUndo!: (value: unknown) => void;
    mocks.undoChange.mockReturnValue(new Promise((resolve) => (resolveUndo = resolve)));
    const session = createCommandSession();
    session.apply(memoryChange());

    const first = session.undoChange('change-1');
    expect(await session.undoChange('change-1')).toBe(false);
    expect(mocks.undoChange).toHaveBeenCalledTimes(1);

    resolveUndo(undoResult);
    expect(await first).toBe(true);
    expect(session.changeUndoing).toBe(false);
  });

  it('clears a previous error at the start of the next attempt', async () => {
    mocks.undoChange.mockRejectedValueOnce(new Error('CHANGE_NOT_FOUND')).mockResolvedValueOnce(undoResult);
    const session = createCommandSession();
    session.apply(memoryChange());

    expect(await session.undoChange('change-1')).toBe(false);
    expect(session.changeError).toBe('That change is no longer available.');

    expect(await session.undoChange('change-1')).toBe(true);
    expect(session.changeError).toBe('');
  });

  it('a new change replaces the old toast and clears an old error', async () => {
    mocks.undoChange.mockRejectedValue(new Error('CHANGE_NOT_FOUND'));
    const session = createCommandSession();
    session.apply(memoryChange('change-1'));
    expect(await session.undoChange('change-1')).toBe(false);
    expect(session.changeError).not.toBe('');

    session.apply(memoryChange('change-2', 'Remembered: drinks tea.'));

    expect(session.change).toMatchObject({ id: 'change-2', label: 'Remembered: drinks tea.' });
    expect(session.changeError).toBe('');
  });
});
