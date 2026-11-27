import { describe, expect, it } from 'vitest';
import { initialVoiceState } from './connection';
import { deriveStage } from './stageView';

describe('deriveStage', () => {
  it('uses the supplied resolved theme instead of defaulting', () => {
    const light = deriveStage(initialVoiceState(), false, 'none', 'light');
    const dark = deriveStage(initialVoiceState(), false, 'none', 'dark');
    expect(light.presentation.theme).toBe('light');
    expect(dark.presentation.theme).toBe('dark');
    expect(light.presentation.primaryColor).not.toBe(dark.presentation.primaryColor);
  });

  it('resolves a dev overlay over the derived phase', () => {
    const stage = deriveStage(initialVoiceState(), false, 'memory_work', 'dark');
    expect(stage.presentation.effectivePhase).toBe('idle');
    expect(stage.presentation.accent).not.toBeNull();
    expect(stage.presentation.ariaLabel.en).toContain('Checking memory');
  });

  it('lets a fatal error outrank the phase and offers retry', () => {
    const live = {
      id: 'sess-1',
      url: 'ws://fake.invalid',
      token: 'token',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      voice: { id: 'voice-1', name: 'Voice', languages: [] },
      voice_language: 'en' as const,
      voice_source: 'default' as const,
    };
    const voice = {
      ...initialVoiceState(),
      live,
      agentState: 'listening' as const,
      voiceErrorCode: 'CONNECTION_LOST',
    };
    const stage = deriveStage(voice, false, 'none', 'light');
    expect(stage.presentation.effectivePhase).toBe('error');
    expect(stage.presentation.enabledActions).toEqual(['retry']);
  });

  it('shows the disconnected state when the connection is lost', () => {
    const voice = { ...initialVoiceState(), agentState: 'speaking' as const, connectionLost: true };
    const stage = deriveStage(voice, false, 'none', 'dark');
    expect(stage.presentation.effectivePhase).toBe('disconnected');
    expect(stage.presentation.statusLabel.en).toBe('Offline');
    expect(stage.presentation.statusLabel.ar).toBe('غير متصل');
  });

  it('announces "Reconnecting…" while the live transport reconnects', () => {
    const live = {
      id: 'sess-1',
      url: 'ws://fake.invalid',
      token: 'token',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      voice: { id: 'voice-1', name: 'Voice', languages: [] },
      voice_language: 'en' as const,
      voice_source: 'default' as const,
    };
    const stage = deriveStage({ ...initialVoiceState(), live, transportStatus: 'reconnecting' }, false, 'none', 'dark');
    expect(stage.presentation.effectivePhase).toBe('connecting');
    expect(stage.presentation.statusLabel.en).toBe('Reconnecting…');
    expect(stage.presentation.statusLabel.ar).toBe('جارٍ إعادة الاتصال…');
  });

  it('announces Offline when a lost connection also has a fatal error', () => {
    const stage = deriveStage(
      { ...initialVoiceState(), connectionLost: true, voiceErrorCode: 'CONNECTION_LOST' },
      false,
      'none',
      'dark',
    );
    expect(stage.presentation.effectivePhase).toBe('error');
    expect(stage.presentation.statusLabel.en).toBe('Offline');
    expect(stage.presentation.statusLabel.ar).toBe('غير متصل');
  });
});

describe('deriveStage error priority', () => {
  const live = {
    id: 'sess-1',
    url: 'ws://fake.invalid',
    token: 'token',
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    voice: { id: 'voice-1', name: 'Voice', languages: [] },
    voice_language: 'en' as const,
    voice_source: 'default' as const,
  };

  it('keeps the live phase and its actions for a recoverable error', () => {
    const stage = deriveStage(
      {
        ...initialVoiceState(),
        live,
        muted: false,
        agentState: 'listening',
        voiceErrorCode: 'MICROPHONE_PERMISSION_DENIED',
      },
      false,
      'none',
      'dark',
    );
    expect(stage.presentation.effectivePhase).toBe('listening');
    expect(stage.presentation.enabledActions).toEqual(['mute', 'end']);
    expect(stage.presentation.enabledActions).not.toContain('retry');
    expect(stage.errorText).toContain('Microphone access is blocked');
  });

  it.each([
    'CONNECTION_LOST',
    'SESSION_ENDED',
    'SESSION_EXPIRED',
    'SESSION_RELEASE_FAILED',
    'TOKEN_EXPIRED',
    'ACCOUNT_SESSION_REQUIRED',
    'HTTP_401',
    'HTTP_403',
  ])('keeps the error overlay and retry for fatal %s on a live call', (code) => {
    const stage = deriveStage(
      {
        ...initialVoiceState(),
        live,
        agentState: 'listening',
        voiceErrorCode: code,
      },
      false,
      'none',
      'dark',
    );
    expect(stage.presentation.effectivePhase).toBe('error');
    expect(stage.presentation.enabledActions).toEqual(['retry']);
  });

  it.each(['MICROPHONE_PERMISSION_DENIED', 'TRANSCRIPT_UNAVAILABLE', 'DAILY_BUDGET_EXHAUSTED', 'API_UNAVAILABLE'])(
    'keeps the live phase for non-fatal %s on a live call',
    (code) => {
      const stage = deriveStage(
        {
          ...initialVoiceState(),
          live,
          muted: false,
          agentState: 'listening',
          voiceErrorCode: code,
        },
        false,
        'none',
        'dark',
      );
      expect(stage.presentation.effectivePhase).toBe('listening');
      expect(stage.presentation.enabledActions).toEqual(['mute', 'end']);
    },
  );

  it('shows the error overlay for a recoverable startup error when no call is live', () => {
    const stage = deriveStage(
      { ...initialVoiceState(), voiceErrorCode: 'MICROPHONE_PERMISSION_DENIED' },
      false,
      'none',
      'dark',
    );
    expect(stage.presentation.effectivePhase).toBe('error');
    expect(stage.presentation.enabledActions).toEqual(['retry']);
  });
});

describe('deriveStage connection state colour', () => {
  it('turns the orb red for an error even on a lost connection', () => {
    const lost = deriveStage(
      { ...initialVoiceState(), agentState: 'speaking' as const, connectionLost: true, voiceErrorCode: 'UNKNOWN_CODE' },
      false,
      'none',
      'dark',
    );
    expect(lost.presentation.effectivePhase).toBe('error');
    expect(lost.presentation.primaryColor).toBe('#ff7d9d');
  });

  it('keeps a clean drop on the disconnected palette', () => {
    const lost = deriveStage(
      { ...initialVoiceState(), agentState: 'speaking' as const, connectionLost: true },
      false,
      'none',
      'dark',
    );
    expect(lost.presentation.effectivePhase).toBe('disconnected');
  });
});
