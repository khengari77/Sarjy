import { describe, expect, it, vi } from 'vitest';
import type { VoiceOption } from '../api';
import {
  createOnboardingController,
  initialOnboardingState,
  isSuspended,
  isTerminal,
  stepLabel,
  type OnboardingProgress,
} from './onboarding';

function progress(overrides: Partial<OnboardingProgress> = {}): OnboardingProgress {
  return {
    schema_version: 1,
    revision: 2,
    stage: 'preferred_name',
    preferred_name: null,
    selected_voice_id: null,
    selected_pace: null,
    intro_played_at: '2026-11-14T09:00:00+00:00',
    started_at: '2026-11-14T09:00:00+00:00',
    completed_at: null,
    suspended_from: null,
    last_event_id: 'greeting',
    greeting: null,
    voice_application: null,
    capabilities: { speaking_pace: false, weather: true, memory: true, wordle: true, chat: true },
    ...overrides,
  };
}

describe('onboarding presentation helpers', () => {
  it('labels each question and treats only completed/skipped as terminal', () => {
    expect(stepLabel('voice_choice')).toBe('Your voice');
    expect(isTerminal('completed')).toBe(true);
    expect(isTerminal('skipped')).toBe(true);
    expect(isTerminal('suspended')).toBe(false);
    expect(isSuspended('suspended')).toBe(true);
  });
});

describe('onboarding controller', () => {
  it('loads server progress once the demo session exists', async () => {
    const state = initialOnboardingState();
    const request = vi.fn().mockResolvedValue(progress());
    const controller = createOnboardingController(state, { request });
    await controller.load();
    expect(request).toHaveBeenCalledWith('onboarding');
    expect(state.status).toBe('active');
    expect(state.progress?.stage).toBe('preferred_name');
    expect(controller.active()).toBe(true);
  });

  it('sends typed events with a fresh idempotency id and applies the response', async () => {
    const state = initialOnboardingState();
    state.progress = progress();
    state.status = 'active';
    const request = vi.fn().mockResolvedValue(progress({ stage: 'voice_choice', preferred_name: 'Sam', revision: 3 }));
    const controller = createOnboardingController(state, { request, nextEventId: () => 'event-1' });
    await controller.submitName('Sam');
    expect(request).toHaveBeenCalledWith('onboarding/events', {
      event_id: 'event-1',
      type: 'submit_name',
      name: 'Sam',
    });
    expect(state.progress?.preferred_name).toBe('Sam');
    expect(state.progress?.stage).toBe('voice_choice');
  });

  it('sends a voice choice that applies next call and never claims a live switch', async () => {
    const state = initialOnboardingState();
    state.progress = progress({ stage: 'voice_choice' });
    const request = vi
      .fn()
      .mockResolvedValue(
        progress({ stage: 'capability_intro', selected_voice_id: 'voice_a', voice_application: 'next_call' }),
      );
    const controller = createOnboardingController(state, { request, nextEventId: () => 'event-2' });
    await controller.chooseVoice('voice_a');
    expect(request).toHaveBeenCalledWith('onboarding/events', {
      event_id: 'event-2',
      type: 'choose_voice',
      voice_id: 'voice_a',
    });
    expect(state.progress?.voice_application).toBe('next_call');
  });

  it('resyncs instead of trapping the user when the tab holds a stale stage', async () => {
    const state = initialOnboardingState();
    state.progress = progress();
    const request = vi
      .fn()
      .mockRejectedValueOnce(new Error('ONBOARDING_TRANSITION_INVALID'))
      .mockResolvedValueOnce(progress({ stage: 'capability_intro', revision: 5 }));
    const controller = createOnboardingController(state, { request, nextEventId: () => 'stale' });
    await controller.submitName('Sam');
    expect(state.error).toBe('ONBOARDING_TRANSITION_INVALID');
    expect(state.progress?.stage).toBe('capability_intro');
  });
});

describe('onboarding voice advancement', () => {
  it('ignores partial transcripts without calling the API', async () => {
    const state = initialOnboardingState();
    state.progress = progress();
    state.status = 'active';
    const request = vi.fn();
    const controller = createOnboardingController(state, { request });
    expect(await controller.handleVoiceUtterance({ final: false, speaker: 'user', text: 'Sam', id: 't1' }, [])).toBe(
      false,
    );
    expect(request).not.toHaveBeenCalled();
  });

  it('advances a finalized greeting answer through the same typed events', async () => {
    const state = initialOnboardingState();
    state.progress = progress({ stage: 'greeting', intro_played_at: null });
    state.status = 'active';
    const request = vi
      .fn()
      .mockResolvedValueOnce(progress({ stage: 'preferred_name', revision: 2 }))
      .mockResolvedValueOnce(progress({ stage: 'voice_choice', preferred_name: 'Sam', revision: 3 }));
    const controller = createOnboardingController(state, { request, nextEventId: () => 'voice-1' });
    const handled = await controller.handleVoiceUtterance(
      { final: true, speaker: 'user', text: 'My name is Sam', id: 'turn-9' },
      [],
    );
    expect(handled).toBe(true);
    expect(request.mock.calls.map((call) => call[1].type)).toEqual(['greeting_played', 'submit_name']);
    expect(state.progress?.preferred_name).toBe('Sam');
  });

  it('does not re-apply a replayed final transcript', async () => {
    const state = initialOnboardingState();
    state.progress = progress();
    state.status = 'active';
    const request = vi.fn().mockResolvedValue(progress({ stage: 'voice_choice', preferred_name: 'Sam' }));
    const controller = createOnboardingController(state, { request });
    const utterance = { final: true, speaker: 'user' as const, text: 'Sam', id: 'turn-7' };
    expect(await controller.handleVoiceUtterance(utterance, [])).toBe(true);
    expect(await controller.handleVoiceUtterance(utterance, [])).toBe(false);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('suspends on an unrelated finalized request and chooses a catalog voice by name', async () => {
    const voices: VoiceOption[] = [{ id: 'voice_warm', name: 'Warm Friend', languages: ['en'] }];
    const state = initialOnboardingState();
    state.progress = progress({ stage: 'voice_choice' });
    const request = vi
      .fn()
      .mockResolvedValueOnce(progress({ stage: 'suspended', suspended_from: 'voice_choice' }))
      .mockResolvedValueOnce(progress({ stage: 'capability_intro', selected_voice_id: 'voice_warm' }));
    const controller = createOnboardingController(state, { request });
    expect(
      await controller.handleVoiceUtterance(
        { final: true, speaker: 'user', text: 'What is the weather?', id: 't8' },
        voices,
      ),
    ).toBe(true);
    expect(state.progress?.stage).toBe('suspended');
    state.progress = progress({ stage: 'voice_choice' });
    await controller.handleVoiceUtterance({ final: true, speaker: 'user', text: 'Warm Friend', id: 't9' }, voices);
    expect(request.mock.calls.at(-1)?.[1]).toEqual({
      event_id: expect.any(String),
      type: 'choose_voice',
      voice_id: 'voice_warm',
    });
  });

  it('stops being active after skip or suspension', async () => {
    const state = initialOnboardingState();
    state.progress = progress();
    const request = vi
      .fn()
      .mockResolvedValueOnce(progress({ stage: 'skipped' }))
      .mockResolvedValueOnce(progress({ stage: 'suspended', suspended_from: 'preferred_name' }));
    const controller = createOnboardingController(state, { request });
    await controller.skip();
    expect(controller.active()).toBe(false);
    state.progress = progress();
    await controller.suspend();
    expect(controller.active()).toBe(false);
    expect(isSuspended(state.progress?.stage)).toBe(true);
  });
});
