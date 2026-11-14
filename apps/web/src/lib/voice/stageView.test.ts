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
    const voice = { ...initialVoiceState(), voiceErrorCode: 'PIPELINE_NOT_CONFIGURED' };
    const stage = deriveStage(voice, false, 'none', 'light');
    expect(stage.presentation.effectivePhase).not.toBe('idle');
    expect(stage.presentation.enabledActions).toEqual(['retry']);
  });
});
