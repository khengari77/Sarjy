import { describe, expect, it } from 'vitest';
import {
  createCommandStore,
  initialCommandState,
  normalizeCommandResult,
  type CommandResult,
  type SettingsSnapshot,
} from './commandStore';

function settings(overrides: Partial<SettingsSnapshot> = {}): SettingsSnapshot {
  return {
    speaking_speed: 1,
    captions_visible: true,
    approved_voice_id: null,
    response_preference: 'automatic',
    memory_mode: 'auto',
    revision: 0,
    ...overrides,
  };
}

function voiceCaptions(overrides: Partial<CommandResult> = {}): CommandResult {
  return {
    request_id: 'command:abc',
    kind: 'executed',
    action: 'SetCaptionsVisible',
    settings: settings({ captions_visible: false, revision: 1 }),
    navigation: null,
    theme: null,
    changed: true,
    spoken: 'Captions hidden.',
    detail: null,
    ...overrides,
  };
}

function memoryChange(overrides: Partial<CommandResult> = {}): CommandResult {
  return {
    request_id: 'memauto:change-1',
    kind: 'executed',
    action: 'MemoryChange',
    settings: null,
    navigation: null,
    theme: null,
    changed: true,
    spoken: 'Remembered: prefers oat milk.',
    detail: null,
    change: {
      id: 'change-1',
      kind: 'memory_saved',
      label: 'Remembered: prefers oat milk.',
      undoable: true,
      memory_id: 'memory-1',
    },
    ...overrides,
  };
}

function themeCommand(): CommandResult {
  return {
    request_id: 'command:theme-1',
    kind: 'executed',
    action: 'SetTheme',
    settings: null,
    navigation: null,
    theme: 'light',
    changed: true,
    spoken: 'Light mode on.',
    detail: null,
  };
}

function harness() {
  const state = initialCommandState();
  const store = createCommandStore(state);
  return { state, store };
}

describe('memory change results', () => {
  it('accepts the auto-save payload with null settings', () => {
    const normalized = normalizeCommandResult(memoryChange());
    expect(normalized?.change).toEqual({
      id: 'change-1',
      kind: 'memory_saved',
      label: 'Remembered: prefers oat milk.',
      undoable: true,
      memory_id: 'memory-1',
    });
    expect(normalized?.settings).toBeNull();
  });

  it('requires a settings snapshot for settings-bearing actions only', () => {
    for (const action of [
      'SelectApprovedVoice',
      'AdjustSpeakingSpeed',
      'SetSpeakingSpeed',
      'SetCaptionsVisible',
      'SetMemoryMode',
    ]) {
      expect(normalizeCommandResult(voiceCaptions({ action, settings: null }))).toBeNull();
    }
    expect(normalizeCommandResult(themeCommand())).not.toBeNull();
    expect(normalizeCommandResult(memoryChange())).not.toBeNull();
  });

  it('rejects malformed change payloads', () => {
    const base = memoryChange();
    expect(normalizeCommandResult({ ...base, change: null })).toBeNull();
    expect(normalizeCommandResult({ ...base, change: { ...base.change, id: '' } })).toBeNull();
    expect(normalizeCommandResult({ ...base, change: { ...base.change, kind: 'memory_deleted' } })).toBeNull();
    expect(normalizeCommandResult({ ...base, change: { ...base.change, label: '' } })).toBeNull();
    expect(normalizeCommandResult({ ...base, change: { ...base.change, undoable: 'yes' } })).toBeNull();
    expect(normalizeCommandResult({ ...base, change: { ...base.change, memory_id: 7 } })).toBeNull();
    expect(normalizeCommandResult({ ...base, change: { ...base.change, memory_id: null } })).not.toBeNull();
    expect(normalizeCommandResult({ ...voiceCaptions(), change: { ...base.change, undoable: 'yes' } })).toBeNull();
  });

  it('leaves settings and their revision untouched, then exposes and dismisses the change', () => {
    const { store, state } = harness();
    store.hydrate(settings({ revision: 4 }));
    store.apply(memoryChange({ settings: settings({ revision: 1, captions_visible: false }) }));

    expect(state.settings).toEqual(settings({ revision: 4 }));
    expect(state.change?.id).toBe('change-1');
    expect(state.last?.action).toBe('MemoryChange');

    store.dismissChange();
    expect(state.change).toBeNull();
  });

  it('dedupes by request id and a new change replaces the old toast', () => {
    const { store, state } = harness();
    expect(store.apply(memoryChange())).toBe(true);
    expect(
      store.apply(
        memoryChange({
          change: { ...memoryChange().change!, label: 'A different label' },
        }),
      ),
    ).toBe(false);
    expect(state.change?.label).toBe('Remembered: prefers oat milk.');

    store.apply(
      memoryChange({
        request_id: 'memauto:change-2',
        change: {
          id: 'change-2',
          kind: 'memory_updated',
          label: 'Remembered: drinks tea.',
          undoable: true,
          memory_id: 'memory-1',
        },
      }),
    );
    expect(state.change).toMatchObject({ id: 'change-2', kind: 'memory_updated' });
    expect(state.trace).toEqual(['MemoryChange', 'MemoryChange']);
  });
});
