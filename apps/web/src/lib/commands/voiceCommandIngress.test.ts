import { describe, expect, it, vi } from 'vitest';
import type { VoiceEvent, VoiceRuntime } from '../audio/voice-runtime';
import { createVoiceConnection, initialVoiceState } from '../voice/connection';
import { createCommandStore, initialCommandState, type CommandResult } from './commandStore';

const runtime = {
  epoch: 0,
  subscribe: () => () => {},
} as unknown as VoiceRuntime;

function command(): CommandResult {
  return {
    request_id: 'command:turn-1',
    kind: 'executed',
    action: 'SetCaptionsVisible',
    settings: {
      speaking_speed: 1,
      captions_visible: false,
      approved_voice_id: null,
      response_preference: 'automatic',
      revision: 1,
    },
    navigation: null,
    changed: true,
    spoken: 'Captions hidden.',
    detail: null,
  };
}

function harness() {
  const state = initialCommandState();
  const store = createCommandStore(state, { send: vi.fn() }, { persist: () => {} });
  const apply = vi.fn((result: CommandResult) => store.apply(result));
  const connection = createVoiceConnection(initialVoiceState(), {
    ensureConversation: async () => 'c1',
    getConversationId: () => 'c1',
    refresh: async () => {},
    getRuntime: () => runtime,
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'v1', name: 'Voice' }),
    applyCommand: apply,
    meters: {
      startMicMeter: vi.fn(),
      stopMicMeter: vi.fn(),
      startOutputMeter: vi.fn(),
      stopOutputMeter: vi.fn(),
      setupMeters: vi.fn(),
      teardownMeters: vi.fn(),
    },
  });
  const emit = (event: Record<string, unknown>) => connection.handleEvent({ ...event, epoch: 0 } as VoiceEvent);
  return { state, store, apply, emit };
}

describe('finalized-only command ingress', () => {
  it('never applies an action for a partial transcript', () => {
    const { state, apply, emit } = harness();
    emit({
      type: 'transcript',
      text: 'Hide captions',
      final: false,
      segmentId: 'seg-1',
      speaker: 'user',
    });
    expect(apply).not.toHaveBeenCalled();
    expect(state.settings.captions_visible).toBe(true);
  });

  it('does not parse a finalized transcript in the browser', () => {
    const { state, apply, emit } = harness();
    emit({
      type: 'transcript',
      text: 'Hide captions.',
      final: true,
      segmentId: 'seg-1',
      speaker: 'user',
    });
    // The worker owns interpretation; a final transcript only refreshes the
    // conversation, it never dispatches an action locally.
    expect(apply).not.toHaveBeenCalled();
    expect(state.settings.captions_visible).toBe(true);
  });

  it('applies a worker result once even when delivery repeats', () => {
    const { state, store, apply, emit } = harness();
    emit({ type: 'command', command: command() });
    emit({ type: 'command', command: command() });
    expect(apply).toHaveBeenCalledTimes(2);
    expect(store.apply(command())).toBe(false);
    expect(state.settings.captions_visible).toBe(false);
    expect(state.trace).toEqual(['SetCaptionsVisible']);
  });
});
