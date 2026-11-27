import { describe, expect, it, vi } from 'vitest';
import { createVoiceConnection, initialVoiceState, type ConnectionDeps, type VoiceState } from './connection';
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
  return { state, runtime, deps };
}

describe('recoverable errors clear after a successful action', () => {
  it('clears on a successful unmute', async () => {
    const { state, deps } = harness();
    await toggleVoiceMute(state, deps);
    expect(state.muted).toBe(false);
    expect(state.voiceErrorCode).toBe('');
  });

  it('keeps the error when the microphone action fails', async () => {
    const { state, deps } = harness();
    vi.mocked(deps.getRuntime().startMicrophone).mockRejectedValueOnce(new Error('MICROPHONE_PERMISSION_DENIED'));
    await expect(toggleVoiceMute(state, deps)).rejects.toThrow('MICROPHONE_PERMISSION_DENIED');
    expect(state.voiceErrorCode).toBe('MICROPHONE_PERMISSION_DENIED');
  });

  it('clears on a successful interrupt', async () => {
    const { state, deps } = harness({ voiceErrorCode: 'TRANSCRIPT_UNAVAILABLE' });
    await interruptVoice(state, deps);
    expect(state.voiceErrorCode).toBe('');
  });

  it('clears on a successful microphone retry without ending the call', async () => {
    const { state, runtime, deps } = harness();
    await retryVoiceMicrophone(state, deps);
    expect(runtime.startMicrophone).toHaveBeenCalledOnce();
    expect(state.live).toEqual(LIVE);
    expect(state.voiceErrorCode).toBe('');
  });

  it('clears on the next successful agent status event', () => {
    const { state, deps } = harness();
    const connection = createVoiceConnection(state, deps);
    connection.handleEvent({ type: 'status', source: 'agent', text: 'listening', epoch: 1 });
    expect(state.voiceErrorCode).toBe('');
  });

  it('does not clear a fatal error on an agent status event', () => {
    const { state, deps } = harness({ voiceErrorCode: 'CONNECTION_LOST' });
    const connection = createVoiceConnection(state, deps);
    connection.handleEvent({ type: 'status', source: 'agent', text: 'listening', epoch: 1 });
    expect(state.voiceErrorCode).toBe('CONNECTION_LOST');
  });
});
