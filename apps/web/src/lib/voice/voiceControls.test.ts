import { describe, expect, it, vi } from 'vitest';
import { createVoiceConnection, initialVoiceState, type ConnectionDeps, type VoiceState } from './connection';
import { setVoiceError } from './errors';
import { interruptVoice, retryVoiceMicrophone, toggleVoiceMute } from './voiceControls';

const LIVE = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  voice: { id: 'voice-1', name: 'Voice', languages: [] },
  voice_language: 'en' as const,
  voice_source: 'default' as const,
};

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res as (value: T | PromiseLike<T>) => void;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function harness(overrides: Partial<VoiceState> = {}) {
  const state: VoiceState = {
    ...initialVoiceState(),
    live: LIVE,
    muted: true,
    agentState: 'listening',
    voiceErrorCode: 'MICROPHONE_PERMISSION_DENIED',
    ...overrides,
  };
  const runtime = {
    startMicrophone: vi.fn(async () => {}),
    stopMicrophone: vi.fn(async () => {}),
    interrupt: vi.fn(async () => {}),
  };
  const meters = {
    startMicMeter: vi.fn(),
    stopMicMeter: vi.fn(),
    startOutputMeter: vi.fn(),
    stopOutputMeter: vi.fn(),
    setupMeters: vi.fn(),
    teardownMeters: vi.fn(),
  };
  const deps = {
    ensureConversation: async () => 'c1',
    getConversationId: () => 'c1',
    refresh: async () => {},
    getRuntime: () => runtime,
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'voice-1', name: 'Voice' }),
    applyCommand: () => {},
    resyncSettings: () => {},
    meters,
  } as unknown as ConnectionDeps;
  return { state, runtime, meters, deps };
}

describe('fatal errors survive successful controls', () => {
  it('keeps SESSION_RELEASE_FAILED through an unmute', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'SESSION_RELEASE_FAILED' });
    await toggleVoiceMute(state, deps);
    expect(state.muted).toBe(false);
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });

  it('keeps SESSION_RELEASE_FAILED through a mute', async () => {
    const { state, deps } = harness({ muted: false, voiceErrorCode: 'SESSION_RELEASE_FAILED' });
    await toggleVoiceMute(state, deps);
    expect(state.muted).toBe(true);
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });

  it('keeps SESSION_RELEASE_FAILED through an interrupt', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'SESSION_RELEASE_FAILED' });
    await interruptVoice(state, deps);
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });

  it('keeps SESSION_RELEASE_FAILED through a microphone retry', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'SESSION_RELEASE_FAILED' });
    await retryVoiceMicrophone(state, deps);
    expect(state.muted).toBe(false);
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });
});

describe('only matching recoverable errors clear', () => {
  it('clears a microphone error after a successful unmute and starts the meter', async () => {
    const { state, meters, deps } = harness();
    await toggleVoiceMute(state, deps);
    expect(state.muted).toBe(false);
    expect(meters.startMicMeter).toHaveBeenCalledOnce();
    expect(state.voiceErrorCode).toBe('');
  });

  it('leaves an agent error after a successful unmute', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'TRANSCRIPT_UNAVAILABLE' });
    await toggleVoiceMute(state, deps);
    expect(state.voiceErrorCode).toBe('TRANSCRIPT_UNAVAILABLE');
  });

  it('clears a microphone error after a successful retry and starts the meter', async () => {
    const { state, runtime, meters, deps } = harness();
    await retryVoiceMicrophone(state, deps);
    expect(runtime.startMicrophone).toHaveBeenCalledOnce();
    expect(state.muted).toBe(false);
    expect(meters.startMicMeter).toHaveBeenCalledOnce();
    expect(state.voiceErrorCode).toBe('');
  });

  it('leaves an agent error after a successful retry', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'TRANSCRIPT_UNAVAILABLE' });
    await retryVoiceMicrophone(state, deps);
    expect(state.voiceErrorCode).toBe('TRANSCRIPT_UNAVAILABLE');
  });

  it('clears an agent error after a successful interrupt', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'TRANSCRIPT_UNAVAILABLE' });
    await interruptVoice(state, deps);
    expect(state.voiceErrorCode).toBe('');
  });

  it('leaves a microphone error after a successful interrupt', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'MICROPHONE_PERMISSION_DENIED' });
    await interruptVoice(state, deps);
    expect(state.voiceErrorCode).toBe('MICROPHONE_PERMISSION_DENIED');
  });
});

describe('microphone actions ignore stale and concurrent results', () => {
  it('ignores a retry that resolves after the call was replaced', async () => {
    const { state, runtime, meters, deps } = harness({ voiceErrorCode: 'MICROPHONE_NOT_FOUND' });
    const pending = deferred();
    runtime.startMicrophone.mockReturnValueOnce(pending.promise);

    const retry = retryVoiceMicrophone(state, deps);
    state.live = { ...LIVE, id: 'sess-2' };
    state.muted = true;
    state.voiceErrorCode = 'MICROPHONE_PERMISSION_DENIED';

    pending.resolve();
    await retry;

    expect(state.muted).toBe(true);
    expect(state.voiceErrorCode).toBe('MICROPHONE_PERMISSION_DENIED');
    expect(meters.startMicMeter).not.toHaveBeenCalled();
  });

  it('ignores an unmute that resolves after the call was replaced', async () => {
    const { state, runtime, meters, deps } = harness({ voiceErrorCode: 'MICROPHONE_PERMISSION_DENIED' });
    const pending = deferred();
    runtime.startMicrophone.mockReturnValueOnce(pending.promise);

    const toggle = toggleVoiceMute(state, deps);
    state.live = { ...LIVE, id: 'sess-2' };
    state.muted = true;
    state.voiceErrorCode = 'MICROPHONE_NOT_FOUND';

    pending.resolve();
    await toggle;

    expect(state.muted).toBe(true);
    expect(state.voiceErrorCode).toBe('MICROPHONE_NOT_FOUND');
    expect(meters.startMicMeter).not.toHaveBeenCalled();
  });

  it('ignores a second retry while the first is pending', async () => {
    const { state, runtime, deps } = harness();
    const pending = deferred();
    runtime.startMicrophone.mockReturnValueOnce(pending.promise);

    const first = retryVoiceMicrophone(state, deps);
    const second = retryVoiceMicrophone(state, deps);
    expect(runtime.startMicrophone).toHaveBeenCalledOnce();

    pending.resolve();
    await Promise.all([first, second]);
    expect(state.muted).toBe(false);
    expect(state.voiceErrorCode).toBe('');
  });
});

describe('microphone failures keep the retry path', () => {
  it('maps an unknown retry failure to MICROPHONE_UNAVAILABLE', async () => {
    const { state, runtime, deps } = harness();
    runtime.startMicrophone.mockRejectedValueOnce(new Error('boom'));
    await expect(retryVoiceMicrophone(state, deps)).resolves.toBeUndefined();
    expect(state.voiceErrorCode).toBe('MICROPHONE_UNAVAILABLE');
  });

  it('maps an unknown mute failure to MICROPHONE_UNAVAILABLE without throwing', async () => {
    const { state, runtime, deps } = harness();
    runtime.startMicrophone.mockRejectedValueOnce(new Error('boom'));
    await expect(toggleVoiceMute(state, deps)).resolves.toBeUndefined();
    expect(state.muted).toBe(true);
    expect(state.voiceErrorCode).toBe('MICROPHONE_UNAVAILABLE');
  });
});

describe('fatal errors are only replaced by fatal errors', () => {
  it('uses setVoiceError for the data-channel error path', () => {
    const { state, deps } = harness({ voiceErrorCode: 'SESSION_RELEASE_FAILED' });
    const connection = createVoiceConnection(state, deps);
    connection.handleEvent({ type: 'error', code: 'MICROPHONE_UNAVAILABLE', epoch: 1 });
    connection.handleEvent({ type: 'error', code: 'DAILY_BUDGET_EXHAUSTED', epoch: 1 });
    connection.handleEvent({ type: 'error', code: 'CONVERSATION_REQUIRED', epoch: 1 });
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');

    connection.handleEvent({ type: 'error', code: 'CONNECTION_LOST', epoch: 1 });
    expect(state.voiceErrorCode).toBe('CONNECTION_LOST');
  });

  it('replaces a recoverable error with a fatal one', () => {
    const state = { voiceErrorCode: 'MICROPHONE_UNAVAILABLE' };
    setVoiceError(state, 'SESSION_RELEASE_FAILED');
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });

  it('keeps a fatal error against a recoverable replacement', () => {
    const state = { voiceErrorCode: 'SESSION_RELEASE_FAILED' };
    setVoiceError(state, 'MICROPHONE_UNAVAILABLE');
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });
});

describe('healthy agent status clears only agent/transport errors', () => {
  it.each(['idle', 'listening', 'thinking', 'speaking'])('clears TRANSCRIPT_UNAVAILABLE on %s', (text) => {
    const { state, deps } = harness({ voiceErrorCode: 'TRANSCRIPT_UNAVAILABLE' });
    const connection = createVoiceConnection(state, deps);
    connection.handleEvent({ type: 'status', source: 'agent', text, epoch: 1 });
    expect(state.voiceErrorCode).toBe('');
  });

  it('clears a budget error on idle', () => {
    const { state, deps } = harness({ voiceErrorCode: 'DAILY_BUDGET_EXHAUSTED' });
    const connection = createVoiceConnection(state, deps);
    connection.handleEvent({ type: 'status', source: 'agent', text: 'idle', epoch: 1 });
    expect(state.voiceErrorCode).toBe('');
  });

  it('never clears a microphone error on a turn boundary', () => {
    const { state, deps } = harness({ voiceErrorCode: 'MICROPHONE_PERMISSION_DENIED' });
    const connection = createVoiceConnection(state, deps);
    connection.handleEvent({ type: 'status', source: 'agent', text: 'idle', epoch: 1 });
    expect(state.voiceErrorCode).toBe('MICROPHONE_PERMISSION_DENIED');
  });

  it('does not clear a fatal error', () => {
    const { state, deps } = harness({ voiceErrorCode: 'SESSION_RELEASE_FAILED' });
    const connection = createVoiceConnection(state, deps);
    connection.handleEvent({ type: 'status', source: 'agent', text: 'idle', epoch: 1 });
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });
});
