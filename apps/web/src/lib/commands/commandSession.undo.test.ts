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

beforeEach(() => {
  mocks.fetchSettings.mockReset();
  mocks.saveCaptionSelection.mockReset();
  mocks.saveMemoryMode.mockReset();
  mocks.saveSpeakingSpeed.mockReset();
  mocks.undoChange.mockReset();
});

describe('undo failure messages by detail or status', () => {
  it.each([
    ['CHANGE_NOT_FOUND', 'That change is no longer available.'],
    ['HTTP_404', 'That change is no longer available.'],
    ['CHANGE_SUPERSEDED', 'That memory changed since, so I left it alone.'],
    ['HTTP_409', 'That memory changed since, so I left it alone.'],
    ['HTTP_500', "Couldn't undo that change. Nothing was changed."],
  ])('maps %s to a friendly message', async (detail, message) => {
    mocks.undoChange.mockRejectedValue(new Error(detail));
    const session = createCommandSession();
    session.apply(memoryChange());

    expect(await session.undoChange('change-1')).toBe(false);

    expect(session.change?.id).toBe('change-1');
    expect(session.changeError).toBe(message);
  });
});

describe('undo results are scoped to their change', () => {
  it('drops an undo failure when a newer change replaced the target', async () => {
    let rejectUndo!: (error: unknown) => void;
    mocks.undoChange.mockReturnValue(new Promise((_resolve, reject) => (rejectUndo = reject)));
    const session = createCommandSession();
    session.apply(memoryChange('change-1'));
    const pending = session.undoChange('change-1');

    session.apply(memoryChange('change-2', 'Remembered: drinks tea.'));
    expect(session.changeUndoing).toBe(false);
    rejectUndo(new Error('CHANGE_SUPERSEDED'));

    expect(await pending).toBe(false);
    expect(session.change?.id).toBe('change-2');
    expect(session.changeError).toBe('');
  });

  it('lets a newer change be undone while an earlier undo is still pending', async () => {
    let resolveFirst!: (value: unknown) => void;
    mocks.undoChange
      .mockReturnValueOnce(new Promise((resolve) => (resolveFirst = resolve)))
      .mockResolvedValueOnce({ ...undoResult, id: 'change-2' });
    const session = createCommandSession();
    session.apply(memoryChange('change-1'));
    const first = session.undoChange('change-1');

    session.apply(memoryChange('change-2', 'Remembered: drinks tea.'));
    expect(session.changeUndoing).toBe(false);

    expect(await session.undoChange('change-2')).toBe(true);
    expect(mocks.undoChange).toHaveBeenCalledTimes(2);

    resolveFirst(undoResult);
    expect(await first).toBe(true);
    expect(session.change).toBeNull();
  });

  it('does not attach Undone to a newer change when the earlier undo succeeds', async () => {
    let resolveUndo!: (value: unknown) => void;
    mocks.undoChange.mockReturnValue(new Promise((resolve) => (resolveUndo = resolve)));
    const session = createCommandSession();
    session.apply(memoryChange('change-1'));
    const pending = session.undoChange('change-1');

    session.apply(memoryChange('change-2', 'Remembered: drinks tea.'));
    resolveUndo(undoResult);

    expect(await pending).toBe(true);
    expect(session.change?.id).toBe('change-2');
    expect(session.changeStatus).toBe('');
  });

  it('still reports Undone when the toast was dismissed during the undo', async () => {
    let resolveUndo!: (value: unknown) => void;
    mocks.undoChange.mockReturnValue(new Promise((resolve) => (resolveUndo = resolve)));
    const session = createCommandSession();
    session.apply(memoryChange('change-1'));
    const pending = session.undoChange('change-1');

    session.dismissChange();
    resolveUndo(undoResult);

    expect(await pending).toBe(true);
    expect(session.change).toBeNull();
    expect(session.changeStatus).toBe('Undone.');
  });
});
