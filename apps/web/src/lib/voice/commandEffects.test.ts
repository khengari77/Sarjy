import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import type { CommandResult } from '../commands/commandStore';
import { END_AFTER_SPEECH_CAP_MS, createVoiceCommandEffects } from './commandEffects';
import { initialVoiceState, type VoiceState } from './connection';

const SESSION: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  voice: { id: 'voice-1', name: 'Voice', languages: [] },
  voice_language: 'en',
  voice_source: 'default',
};

function liveState(overrides: Partial<VoiceState> = {}): VoiceState {
  return { ...initialVoiceState(), live: SESSION, muted: false, agentState: 'speaking', ...overrides };
}

function result(
  action: string,
  spoken: string,
  requestId = `command:${action}`,
  kind: CommandResult['kind'] = 'executed',
): CommandResult {
  return {
    request_id: requestId,
    kind,
    action,
    settings: null,
    navigation: null,
    theme: null,
    changed: true,
    spoken,
    detail: null,
  };
}

function harness(state: VoiceState, applied = true) {
  const order: string[] = [];
  const apply = vi.fn(() => applied);
  const end = vi.fn(async () => {
    order.push('end');
    state.live = null;
  });
  const connect = vi.fn(async () => {
    order.push('connect');
  });
  const mute = vi.fn(() => {
    order.push('mute');
  });
  const newConversation = vi.fn(async (): Promise<boolean> => {
    order.push('new');
    return true;
  });
  const effects = createVoiceCommandEffects({ state, apply, end, connect, mute, newConversation });
  return { effects, apply, end, connect, mute, newConversation, order };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('EndCall', () => {
  it('waits for the spoken reply to end before ending the call', async () => {
    vi.useFakeTimers();
    const state = liveState({ agentState: 'speaking' });
    const { effects, apply, end } = harness(state);

    effects.apply(result('EndCall', 'Ending the call.'));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ spoken: 'Ending the call.' }));
    expect(end).not.toHaveBeenCalled();

    state.agentState = 'listening';
    effects.onAgentState();
    await vi.advanceTimersByTimeAsync(0);
    expect(end).toHaveBeenCalledTimes(1);
  });

  it('ends at the six-second cap when no state transition arrives', async () => {
    vi.useFakeTimers();
    const state = liveState({ agentState: 'speaking' });
    const { effects, end } = harness(state);

    effects.apply(result('EndCall', 'Ending the call.'));
    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);
    expect(end).toHaveBeenCalledTimes(1);
  });

  it('replies without ending when there is no live call', () => {
    const state = initialVoiceState();
    const { effects, apply, end } = harness(state);

    effects.apply(result('EndCall', 'Ending the call.'));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ spoken: 'There is no call to end.' }));
    expect(end).not.toHaveBeenCalled();
  });
});

describe('MuteMicrophone', () => {
  it('mutes an unmuted live microphone once', () => {
    const state = liveState({ muted: false });
    const { effects, apply, mute } = harness(state);

    effects.apply(result('MuteMicrophone', 'Muted. Tap the microphone to unmute.'));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ spoken: 'Muted. Tap the microphone to unmute.' }));
    expect(mute).toHaveBeenCalledTimes(1);
  });

  it('never toggles an already muted microphone back on', () => {
    const state = liveState({ muted: true });
    const { effects, apply, mute } = harness(state);

    effects.apply(result('MuteMicrophone', 'Muted. Tap the microphone to unmute.'));
    expect(mute).not.toHaveBeenCalled();
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ spoken: 'Muted. Tap the microphone to unmute.' }));
  });

  it('replies without muting when there is no live call', () => {
    const state = initialVoiceState();
    const { effects, apply, mute } = harness(state);

    effects.apply(result('MuteMicrophone', 'Muted. Tap the microphone to unmute.'));
    expect(mute).not.toHaveBeenCalled();
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ spoken: 'There is no call to mute.' }));
  });
});

describe('NewConversation', () => {
  it('ends the live call after its reply, creates the chat, then reconnects', async () => {
    vi.useFakeTimers();
    const state = liveState({ agentState: 'speaking' });
    const { effects, apply, end, connect, order } = harness(state);

    effects.apply(result('NewConversation', 'Starting a new conversation.'));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ spoken: 'Starting a new conversation.' }));
    expect(end).not.toHaveBeenCalled();

    state.agentState = 'listening';
    effects.onAgentState();
    await vi.advanceTimersByTimeAsync(0);
    expect(order).toEqual(['end', 'new', 'connect']);
    expect(connect).toHaveBeenCalledWith(true);
  });

  it('creates and selects a chat without reconnecting when no call was live', async () => {
    const state = initialVoiceState();
    const { effects, end, connect, newConversation } = harness(state);

    effects.apply(result('NewConversation', 'Starting a new conversation.'));
    expect(newConversation).toHaveBeenCalledTimes(1);
    expect(end).not.toHaveBeenCalled();
    expect(connect).not.toHaveBeenCalled();
  });

  it('does not reconnect when the chat could not be created', async () => {
    vi.useFakeTimers();
    const state = liveState({ agentState: 'speaking' });
    const { effects, end, connect, newConversation } = harness(state);
    newConversation.mockResolvedValueOnce(false);

    effects.apply(result('NewConversation', 'Starting a new conversation.'));
    state.agentState = 'listening';
    effects.onAgentState();
    await vi.advanceTimersByTimeAsync(0);
    expect(end).toHaveBeenCalledTimes(1);
    expect(newConversation).toHaveBeenCalledTimes(1);
    expect(connect).not.toHaveBeenCalled();
  });

  it('applies no second effect for a replayed request id', () => {
    const state = initialVoiceState();
    const { effects, apply, newConversation } = harness(state, false);

    effects.apply(result('NewConversation', 'Starting a new conversation.'));
    expect(apply).toHaveBeenCalledTimes(1);
    expect(newConversation).not.toHaveBeenCalled();
  });
});

describe('non-executed results', () => {
  it.each([
    ['EndCall', 'failed'],
    ['EndCall', 'unsupported'],
    ['MuteMicrophone', 'failed'],
    ['MuteMicrophone', 'unsupported'],
    ['NewConversation', 'failed'],
    ['NewConversation', 'unsupported'],
  ] as const)('%s with kind %s applies the message but no effect', async (action, kind) => {
    vi.useFakeTimers();
    const state = liveState({ agentState: 'speaking' });
    const { effects, apply, end, connect, mute, newConversation } = harness(state);

    effects.apply(result(action, 'Nothing changed.', `command:${action}:${kind}`, kind));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action, kind, spoken: 'Nothing changed.' }));
    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);
    expect(end).not.toHaveBeenCalled();
    expect(mute).not.toHaveBeenCalled();
    expect(newConversation).not.toHaveBeenCalled();
    expect(connect).not.toHaveBeenCalled();
  });
});

describe('dispose', () => {
  it('cancels a pending EndCall before the cap', async () => {
    vi.useFakeTimers();
    const state = liveState({ agentState: 'speaking' });
    const { effects, end } = harness(state);

    effects.apply(result('EndCall', 'Ending the call.'));
    effects.dispose();
    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);
    expect(end).not.toHaveBeenCalled();
  });

  it('cancels a pending NewConversation before the cap', async () => {
    vi.useFakeTimers();
    const state = liveState({ agentState: 'speaking' });
    const { effects, newConversation, connect } = harness(state);

    effects.apply(result('NewConversation', 'Starting a new conversation.'));
    effects.dispose();
    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);
    expect(newConversation).not.toHaveBeenCalled();
    expect(connect).not.toHaveBeenCalled();
  });
});
