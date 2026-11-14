import { describe, expect, it, vi } from 'vitest';
import type { VoiceControlState } from '../api';
import {
  acceptVoiceControl,
  awaitVoiceControl,
  beginVoiceSwitch,
  failVoiceSwitch,
  initialVoiceSwitchState,
  resetVoiceSwitch,
} from './voiceSwitch';

function ack(overrides: Partial<VoiceControlState> = {}): VoiceControlState {
  return {
    session_id: 'session',
    conversation_id: 'conversation',
    active_voice_id: 'voice-b',
    active_settings_revision: 1,
    request_id: 'r1',
    voice_id: 'voice-b',
    settings_revision: 1,
    status: 'applied',
    error_code: null,
    ...overrides,
  };
}

function pendingState() {
  const state = initialVoiceSwitchState('voice-a', 'Voice A');
  beginVoiceSwitch(state, { requestId: 'r1', voiceId: 'voice-b', voiceName: 'Voice B', revision: 1 });
  return state;
}

describe('voice switch confirmation', () => {
  it('confirms the new active voice only after a matching ACK', () => {
    const state = pendingState();
    expect(state.activeVoiceId).toBe('voice-a');
    expect(acceptVoiceControl(state, ack())).toBe('applied');
    expect(state.activeVoiceId).toBe('voice-b');
    expect(state.activeVoiceName).toBe('Voice B');
    expect(state.status).toBe('idle');
  });

  it('never confirms a different voice or revision than requested', () => {
    const state = pendingState();
    expect(acceptVoiceControl(state, ack({ voice_id: 'voice-c' }))).toBe('ignored');
    expect(acceptVoiceControl(state, ack({ settings_revision: 2 }))).toBe('ignored');
    expect(acceptVoiceControl(state, ack({ active_voice_id: 'voice-c' }))).toBe('ignored');
    expect(state.activeVoiceId).toBe('voice-a');
    expect(state.status).toBe('pending');
  });

  it('ignores stale ACKs from an earlier request or revision', () => {
    const state = pendingState();
    expect(acceptVoiceControl(state, ack({ request_id: 'r0' }))).toBe('ignored');
    expect(acceptVoiceControl(state, ack({ active_settings_revision: 0 }))).toBe('ignored');
    expect(state.activeVoiceId).toBe('voice-a');
  });

  it('keeps the previous voice and exposes an actionable error on worker failure', () => {
    const state = pendingState();
    expect(acceptVoiceControl(state, ack({ status: 'failed', error_code: 'VOICE_SWITCH_FAILED' }))).toBe('failed');
    expect(state.activeVoiceId).toBe('voice-a');
    expect(state.status).toBe('failed');
    expect(state.errorCode).toBe('VOICE_SWITCH_FAILED');
  });

  it('treats a superseded concurrent request as a failure, not a confirmation', () => {
    const state = pendingState();
    expect(acceptVoiceControl(state, ack({ status: 'superseded' }))).toBe('failed');
    expect(state.errorCode).toBe('VOICE_SWITCH_SUPERSEDED');
    expect(state.activeVoiceId).toBe('voice-a');
  });

  it('is idempotent: replaying the same ACK cannot double-apply', () => {
    const state = pendingState();
    expect(acceptVoiceControl(state, ack())).toBe('applied');
    expect(acceptVoiceControl(state, ack())).toBe('ignored');
    expect(state.activeVoiceId).toBe('voice-b');
  });

  it('a newer selection replaces the pending one and the old ACK is ignored', () => {
    const state = pendingState();
    beginVoiceSwitch(state, { requestId: 'r2', voiceId: 'voice-c', voiceName: 'Voice C', revision: 2 });
    expect(acceptVoiceControl(state, ack())).toBe('ignored');
    expect(
      acceptVoiceControl(
        state,
        ack({
          request_id: 'r2',
          voice_id: 'voice-c',
          active_voice_id: 'voice-c',
          settings_revision: 2,
          active_settings_revision: 2,
        }),
      ),
    ).toBe('applied');
    expect(state.activeVoiceId).toBe('voice-c');
  });

  it('reconnect resets to the new session voice and drops pending state', () => {
    const state = pendingState();
    resetVoiceSwitch(state, 'voice-z', 'Voice Z');
    expect(state.status).toBe('idle');
    expect(state.activeVoiceId).toBe('voice-z');
    expect(state.pendingRequestId).toBe('');
    expect(acceptVoiceControl(state, ack())).toBe('ignored');
  });

  it('records a server delivery error without claiming success', () => {
    const state = pendingState();
    failVoiceSwitch(state, 'VOICE_CONTROL_UNAVAILABLE');
    expect(state.status).toBe('failed');
    expect(state.activeVoiceId).toBe('voice-a');
  });
});

describe('ACK polling', () => {
  it('stops as soon as a matching ACK arrives', async () => {
    const state = pendingState();
    const fetchState = vi.fn(async () => ack());
    expect(await awaitVoiceControl(state, { fetchState, sleep: async () => {} })).toBe('applied');
    expect(fetchState).toHaveBeenCalledTimes(1);
  });

  it('keeps the request pending when the worker has not synthesized yet', async () => {
    const state = pendingState();
    const fetchState = vi.fn(async () => ack({ status: 'dispatched' }));
    expect(await awaitVoiceControl(state, { fetchState, attempts: 3, sleep: async () => {} })).toBe('pending');
    expect(state.status).toBe('pending');
  });

  it('ignores a stale ACK while polling and continues until the current one', async () => {
    const state = pendingState();
    const responses = [ack({ request_id: 'r0' }), ack()];
    const fetchState = vi.fn(async () => responses.shift()!);
    expect(await awaitVoiceControl(state, { fetchState, sleep: async () => {} })).toBe('applied');
    expect(fetchState).toHaveBeenCalledTimes(2);
  });
});
