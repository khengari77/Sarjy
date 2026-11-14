import { describe, expect, it, vi } from 'vitest';
import {
  createCommandStore,
  initialCommandState,
  normalizeCommandResult,
  resultFromUiResponse,
  type ActionPayload,
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

function voiceResult(overrides: Partial<CommandResult> = {}): CommandResult {
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

function harness(result: CommandResult | null, reject = false) {
  const state = initialCommandState();
  const persist = vi.fn();
  const revisions: number[] = [];
  const send = vi.fn(async (_action: ActionPayload, requestId: string, revision: number) => {
    revisions.push(revision);
    if (reject) throw new Error('HTTP_409');
    return result ? { ...result, request_id: requestId } : null;
  });
  const store = createCommandStore(state, { send }, { persist });
  return { state, persist, revisions, send, store };
}

describe('one shared store for GUI and voice', () => {
  it('converges on the same settings, panels and typed-action trace', async () => {
    const gui = harness(voiceResult());
    await gui.store.setCaptions(false);

    const voice = harness(null);
    voice.store.apply(voiceResult());

    expect(gui.state.settings).toEqual(voice.state.settings);
    expect(gui.state.panels).toEqual(voice.state.panels);
    expect(gui.state.trace).toEqual(voice.state.trace);
    expect(gui.state.last?.kind).toBe(voice.state.last?.kind);
    expect(gui.state.last?.action).toBe(voice.state.last?.action);
    expect(gui.persist).toHaveBeenLastCalledWith(gui.state.settings);
    expect(voice.persist).toHaveBeenLastCalledWith(voice.state.settings);
  });

  it('sends a one-step slider move as the same adjustment the voice command uses', async () => {
    const { store, send } = harness(
      voiceResult({ action: 'AdjustSpeakingSpeed', settings: settings({ speaking_speed: 0.9, revision: 1 }) }),
    );
    await store.setSpeed(0.9);
    expect(send).toHaveBeenCalledWith({ kind: 'AdjustSpeakingSpeed', delta: -0.1 }, expect.any(String), 0);
  });

  it('sends a multi-step slider move as an absolute setting', async () => {
    const { store, send } = harness(
      voiceResult({ action: 'SetSpeakingSpeed', settings: settings({ speaking_speed: 0.8, revision: 1 }) }),
    );
    await store.setSpeed(0.8);
    expect(send).toHaveBeenCalledWith({ kind: 'SetSpeakingSpeed', speed: 0.8 }, expect.any(String), 0);
  });

  it('serializes gestures so the second expected revision follows the first result', async () => {
    const state = initialCommandState();
    const send = vi
      .fn()
      .mockImplementationOnce(async (action: ActionPayload, requestId: string) => ({
        ...voiceResult(),
        request_id: requestId,
        settings: settings({ captions_visible: false, revision: 1 }),
      }))
      .mockImplementationOnce(async (action: ActionPayload, requestId: string) => ({
        ...voiceResult({ action: 'AdjustSpeakingSpeed' }),
        request_id: requestId,
        settings: settings({ captions_visible: false, speaking_speed: 0.9, revision: 2 }),
      }));
    const store = createCommandStore(state, { send }, { persist: () => {} });
    await Promise.all([store.setCaptions(false), store.setSpeed(0.9)]);
    expect(send.mock.calls.map((call) => call[2])).toEqual([0, 1]);
    expect(state.settings).toMatchObject({ captions_visible: false, speaking_speed: 0.9, revision: 2 });
  });

  it('ignores a duplicate result for the same request id', () => {
    const { store, state, persist } = harness(null);
    expect(store.apply(voiceResult())).toBe(true);
    expect(store.apply(voiceResult())).toBe(false);
    expect(state.trace).toEqual(['SetCaptionsVisible']);
    expect(state.applied).toEqual(['command:abc']);
    expect(persist).toHaveBeenCalledTimes(1);
  });

  it('reports a failed transfer without changing state', async () => {
    const { store, state } = harness(null, true);
    await store.setCaptions(false);
    expect(state.settings.captions_visible).toBe(true);
    expect(state.trace).toEqual([]);
    expect(state.last).toMatchObject({ kind: 'failed', action: 'SetCaptionsVisible' });
    expect(state.last?.spoken).toContain("couldn't apply");
  });

  it('opens the same panel state for a voice navigation result', () => {
    const { store, state } = harness(null);
    store.apply(
      voiceResult({
        action: 'ShowConversationHistory',
        settings: null,
        navigation: 'history',
        spoken: 'Opening your previous conversations.',
      }),
    );
    expect(state.panels).toEqual({ history: true, memory_explorer: false });
    expect(state.trace).toEqual(['ShowConversationHistory']);
  });
});

describe('result validation', () => {
  it('accepts a worker-authored result and drops malformed payloads', () => {
    expect(normalizeCommandResult(voiceResult())).toEqual(voiceResult());
    expect(normalizeCommandResult({ ...voiceResult(), kind: 'made_up' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceResult(), request_id: '' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceResult(), spoken: '' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceResult(), changed: 'yes' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceResult(), navigation: 'elsewhere' })).toBeNull();
    expect(normalizeCommandResult({ ...voiceResult(), settings: { speaking_speed: 'fast' } })).toBeNull();
    expect(normalizeCommandResult(null)).toBeNull();
  });

  it('maps the typed GUI response to the worker contract', () => {
    const mapped = resultFromUiResponse('ui:1', {
      handled: true,
      result: {
        kind: 'SetCaptionsVisible',
        settings: settings({ captions_visible: false, revision: 1 }),
        changed: true,
      },
    });
    expect(mapped).toMatchObject({
      request_id: 'ui:1',
      kind: 'executed',
      action: 'SetCaptionsVisible',
      settings: { captions_visible: false, revision: 1 },
      changed: true,
    });
    expect(resultFromUiResponse('ui:2', { handled: false })).toBeNull();
    expect(resultFromUiResponse('ui:3', { handled: true, result: {} })).toBeNull();
  });

  it('maps navigation without inventing a settings snapshot', () => {
    const mapped = resultFromUiResponse('ui:4', {
      handled: true,
      result: { kind: 'ShowConversationHistory', navigation: 'history' },
    });
    expect(mapped).toMatchObject({
      action: 'ShowConversationHistory',
      navigation: 'history',
      settings: null,
      kind: 'executed',
    });
  });
});
