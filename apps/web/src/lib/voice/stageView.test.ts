import { describe, expect, it } from 'vitest';
import { initialVoiceState } from './connection';
import { deriveStage } from './stageView';
import { resolvePhaseColor } from '../orb/orbState';

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
    const voice = { ...initialVoiceState(), voiceErrorCode: 'PIPELINE_NOT_CONFIGURED' };
    const stage = deriveStage(voice, false, 'none', 'light');
    expect(stage.presentation.effectivePhase).not.toBe('idle');
    expect(stage.presentation.enabledActions).toEqual(['retry']);
  });

  it('shows the disconnected state when the connection is lost', () => {
    const voice = { ...initialVoiceState(), agentState: 'speaking' as const, connectionLost: true };
    const stage = deriveStage(voice, false, 'none', 'dark');
    expect(stage.presentation.effectivePhase).toBe('disconnected');
    expect(stage.presentation.statusLabel.en).toBe('Connection lost');
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
