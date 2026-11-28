import { describe, expect, it } from 'vitest';
import { createVoiceConnection, initialVoiceState, type ConnectionDeps } from './connection';
import { isFatalVoiceError, setVoiceError } from './errors';

const deps = { meters: { teardownMeters: () => {} } } as unknown as ConnectionDeps;

describe('fatal errors are only replaced by fatal errors', () => {
  it('treats GREETING_UNCONNECTED as fatal', () => {
    expect(isFatalVoiceError('GREETING_UNCONNECTED')).toBe(true);
  });

  it('uses setVoiceError for the data-channel error path', () => {
    const state = { ...initialVoiceState(), voiceErrorCode: 'SESSION_RELEASE_FAILED' };
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
