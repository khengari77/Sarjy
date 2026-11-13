import { describe, expect, it } from 'vitest';
import { friendlyVoiceError, isFatalVoiceError, phaseFromAgentState, phaseFromTransportStatus } from './livekitAdapter';

describe('transport status mapping', () => {
  it('maps connection lifecycle to stable phases', () => {
    expect(phaseFromTransportStatus('connecting')).toBe('connecting');
    expect(phaseFromTransportStatus('reconnecting')).toBe('connecting');
    expect(phaseFromTransportStatus('starting')).toBe('connecting');
    expect(phaseFromTransportStatus('connected')).toBe('listening');
    expect(phaseFromTransportStatus('disconnected')).toBe('disconnected');
    expect(phaseFromTransportStatus('idle')).toBe('idle');
  });

  it('ignores unknown SDK strings instead of painting the orb', () => {
    expect(phaseFromTransportStatus('some-future-event')).toBeNull();
  });
});

describe('agent state mapping', () => {
  it('maps agent phases', () => {
    expect(phaseFromAgentState('listening')).toBe('listening');
    expect(phaseFromAgentState('thinking')).toBe('thinking');
    expect(phaseFromAgentState('speaking')).toBe('speaking');
    expect(phaseFromAgentState('initializing')).toBe('connecting');
  });

  it('treats agent idle as ready for the next user turn', () => {
    expect(phaseFromAgentState('idle')).toBe('listening');
    expect(phaseFromAgentState(undefined)).toBeNull();
  });
});

describe('error handling', () => {
  it('classifies fatal voice errors', () => {
    expect(isFatalVoiceError('PIPELINE_NOT_CONFIGURED')).toBe(true);
    expect(isFatalVoiceError('MICROPHONE_UNAVAILABLE')).toBe(false);
  });

  it('gives bilingual plain-language messages without provider details', () => {
    const en = friendlyVoiceError('MICROPHONE_UNAVAILABLE', false);
    const ar = friendlyVoiceError('MICROPHONE_UNAVAILABLE', true);
    expect(en).toMatch(/microphone/i);
    expect(ar).not.toBe(en);
    expect(friendlyVoiceError('UNKNOWN_CODE', false)).toMatch(/Something went wrong/);
    expect(friendlyVoiceError('UNKNOWN_CODE', false)).not.toMatch(/UNKNOWN_CODE/);
  });
});

describe('microphone permission copy', () => {
  it('gives retry instructions in both languages', () => {
    for (const code of ['MICROPHONE_PERMISSION_DENIED', 'MICROPHONE_NOT_FOUND']) {
      const en = friendlyVoiceError(code, false);
      const ar = friendlyVoiceError(code, true);
      expect(en).not.toBe(ar);
      expect(en).toMatch(/retry|Retry/);
    }
  });
});
