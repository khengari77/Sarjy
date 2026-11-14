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
    changed: true,
    spoken: 'Captions hidden.',
    detail: null,
    ...overrides,
  };
}

function harness() {
  const state = initialCommandState();
  const store = createCommandStore(state);
  return { state, store };
}

describe('one shared store for GUI and voice', () => {
  it('converges captions on the same server snapshot and typed-action trace', () => {
    const gui = harness();
    gui.store.apply(voiceCaptions());

    const voice = harness();
    voice.store.apply(voiceCaptions());

    expect(gui.state.settings).toEqual(voice.state.settings);
    expect(gui.state.trace).toEqual(voice.state.trace);
    expect(gui.state.last).toEqual(voice.state.last);
    expect(gui.state.settings.captions_visible).toBe(false);
    expect(gui.state.settings.revision).toBe(1);
  });

  it('ignores a duplicate result for the same request id', () => {
    const { store, state } = harness();
    expect(store.apply(voiceCaptions())).toBe(true);
    expect(store.apply(voiceCaptions())).toBe(false);
    expect(state.trace).toEqual(['SetCaptionsVisible']);
    expect(state.applied).toEqual(['command:abc']);
  });

  it('a late bootstrap read cannot clobber a newer revision', () => {
    const { store, state } = harness();
    store.hydrate(settings({ captions_visible: false, revision: 2 }));
    store.hydrate(settings({ captions_visible: true, revision: 1 }));
    expect(state.settings).toMatchObject({ captions_visible: false, revision: 2 });
    store.hydrate(settings({ captions_visible: true, revision: 2 }));
    expect(state.settings.captions_visible).toBe(true);
  });

  it('opens the same panel state for a voice navigation result', () => {
    const { store, state } = harness();
    store.apply(
      voiceCaptions({
        action: 'ShowConversationHistory',
        settings: null,
        navigation: 'history',
        spoken: 'Opening your previous conversations.',
      }),
    );
    expect(state.panels).toEqual({ history: true, memory_explorer: false });
    expect(state.trace).toEqual(['ShowConversationHistory']);
  });

  it('an unsupported result changes nothing', () => {
    const { store, state } = harness();
    store.apply({
      request_id: 'command:speed',
      kind: 'unsupported',
      action: null,
      settings: null,
      navigation: null,
      changed: false,
      spoken: "I can't change speaking speed in the call yet. Nothing was changed.",
      detail: 'SPEED_APPLICATION_UNAVAILABLE',
    });
    expect(state.settings).toEqual(settings());
    expect(state.trace).toEqual(['unsupported']);
    expect(state.last?.kind).toBe('unsupported');
  });

  it('a failed GUI gesture records a failure without changing state', () => {
    const { store, state } = harness();
    store.fail('SetCaptionsVisible', 'captions:0:off', 'SETTINGS_REVISION_CONFLICT');
    expect(state.settings).toEqual(settings());
    expect(state.trace).toEqual([]);
    expect(state.last).toMatchObject({ kind: 'failed', action: 'SetCaptionsVisible' });
  });
});

describe('result validation', () => {
  it('accepts a worker-authored result and drops malformed payloads', () => {
    expect(normalizeCommandResult(voiceCaptions())).toEqual(voiceCaptions());
    expect(normalizeCommandResult({ ...voiceCaptions(), kind: 'made_up' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceCaptions(), request_id: '' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceCaptions(), spoken: '' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceCaptions(), changed: 'yes' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceCaptions(), navigation: 'elsewhere' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceCaptions(), settings: { speaking_speed: 'fast' } })).toBeNull();
    expect(normalizeCommandResult(null)).toBeNull();
  });
});
