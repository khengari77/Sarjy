import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import type { VoiceRuntime } from '../audio/voice-runtime';
import type { CommandResult } from '../commands/commandStore';
import { END_AFTER_SPEECH_CAP_MS } from './commandEffects';
import { createVoiceConnection, initialVoiceState, type VoiceState } from './connection';
import type { GreetingPlayer } from './greeting';

const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  end: vi.fn(),
  ensureMic: vi.fn(),
}));

vi.mock('../chat/session', () => ({
  startVoiceSession: mocks.start,
  markIntroComplete: vi.fn(async () => {}),
  endVoiceSession: mocks.end,
}));

vi.mock('../audio/microphone', () => ({ ensureMicrophoneAccess: mocks.ensureMic }));

const SESSION_1: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  voice: { id: 'voice-1', name: 'Voice', languages: [] },
  voice_language: 'en',
  voice_source: 'default',
};
const SESSION_2: SessionConfig = { ...SESSION_1, id: 'sess-2' };

function fakeRuntime() {
  return {
    epoch: 0,
    subscribe: vi.fn(() => () => {}),
    connect: vi.fn(async () => {}),
    disconnect: vi.fn(async () => {}),
    cancelConnect: vi.fn(async () => {}),
    startMicrophone: vi.fn(async () => {}),
    stopMicrophone: vi.fn(async () => {}),
    interrupt: vi.fn(async () => {}),
    sendText: vi.fn(async () => {}),
    say: vi.fn(async () => {}),
    sendControl: vi.fn(async () => {}),
  } as unknown as VoiceRuntime;
}

function meters() {
  return {
    startMicMeter: vi.fn(),
    stopMicMeter: vi.fn(),
    startOutputMeter: vi.fn(),
    stopOutputMeter: vi.fn(),
    setupMeters: vi.fn(),
    teardownMeters: vi.fn(),
  };
}

const greetingPlayer: GreetingPlayer = {
  prepare: vi.fn(),
  play: vi.fn(async () => 'ended' as const),
  stop: vi.fn(),
  playing: false,
};

function command(action: string, spoken: string): CommandResult {
  return {
    request_id: `command:${action}`,
    kind: 'executed',
    action,
    settings: null,
    navigation: null,
    theme: null,
    changed: true,
    spoken,
    detail: null,
  };
}

function harness(options: { newConversation?: () => Promise<boolean | void>; conversationId?: string } = {}) {
  const state: VoiceState = { ...initialVoiceState(), live: SESSION_1, muted: false, agentState: 'speaking' };
  const runtime = fakeRuntime();
  const newConversation = vi.fn(options.newConversation ?? (async () => true));
  const connection = createVoiceConnection(state, {
    ensureConversation: async () => options.conversationId ?? 'c1',
    getConversationId: () => options.conversationId ?? 'c1',
    refresh: async () => {},
    getRuntime: () => runtime,
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'v1', name: 'Voice' }),
    applyCommand: vi.fn(() => true),
    newConversation,
    resyncSettings: () => {},
    meters: meters(),
    greetingPlayer,
  });
  return { connection, state, runtime, newConversation };
}

function emitCommand(connection: ReturnType<typeof createVoiceConnection>, result: CommandResult) {
  connection.handleEvent({ type: 'command', command: result, epoch: 0 });
}

function finishReply(connection: ReturnType<typeof createVoiceConnection>) {
  connection.handleEvent({ type: 'status', source: 'agent', text: 'listening', epoch: 0 });
}

beforeEach(() => {
  mocks.start.mockReset().mockResolvedValue(SESSION_1);
  mocks.end.mockReset().mockResolvedValue(undefined);
  mocks.ensureMic.mockReset().mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe('real connection wiring', () => {
  it('NewConversation ends, creates the chat, and reconnects', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValueOnce(SESSION_2);
    const { connection, state, newConversation } = harness();

    emitCommand(connection, command('NewConversation', 'Starting a new conversation.'));
    expect(newConversation).not.toHaveBeenCalled();
    finishReply(connection);
    await vi.advanceTimersByTimeAsync(100);

    expect(newConversation).toHaveBeenCalledTimes(1);
    expect(mocks.end).toHaveBeenCalledWith(SESSION_1);
    expect(mocks.start).toHaveBeenCalledWith('c1', 'en', true);
    expect(state.live?.id).toBe('sess-2');
  });

  it('aborts NewConversation when the session release fails', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.end.mockRejectedValueOnce(new Error('release failed'));
    const { connection, state, newConversation } = harness();

    emitCommand(connection, command('NewConversation', 'Starting a new conversation.'));
    finishReply(connection);
    await vi.advanceTimersByTimeAsync(100);

    expect(newConversation).not.toHaveBeenCalled();
    expect(mocks.start).not.toHaveBeenCalled();
    expect(state.live?.id).toBe('sess-1');
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
    warn.mockRestore();
  });

  it('aborts NewConversation when the chat could not be created', async () => {
    vi.useFakeTimers();
    const { connection, state, newConversation } = harness({ newConversation: async () => false });

    emitCommand(connection, command('NewConversation', 'Starting a new conversation.'));
    finishReply(connection);
    await vi.advanceTimersByTimeAsync(100);

    expect(newConversation).toHaveBeenCalledTimes(1);
    expect(mocks.start).not.toHaveBeenCalled();
    expect(state.live).toBeNull();
  });

  it('dispose cancels a pending NewConversation', async () => {
    vi.useFakeTimers();
    const { connection, newConversation } = harness();

    emitCommand(connection, command('NewConversation', 'Starting a new conversation.'));
    connection.dispose();
    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);

    expect(newConversation).not.toHaveBeenCalled();
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it('a user end during the running NewConversation callback stops before reconnecting', async () => {
    vi.useFakeTimers();
    let resolveCreate!: (value: boolean) => void;
    const newConversation = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveCreate = resolve;
        }),
    );
    const { connection, state } = harness({ newConversation });

    emitCommand(connection, command('NewConversation', 'Starting a new conversation.'));
    finishReply(connection);
    await vi.advanceTimersByTimeAsync(0);
    expect(newConversation).toHaveBeenCalledTimes(1);
    expect(state.live).toBeNull();

    await connection.end();
    resolveCreate(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(mocks.start).not.toHaveBeenCalled();
    expect(state.live).toBeNull();
  });

  it('end cancels a pending EndCall', async () => {
    vi.useFakeTimers();
    const { connection, runtime } = harness();

    emitCommand(connection, command('EndCall', 'Ending the call.'));
    await connection.end();
    expect(runtime.disconnect).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);
    expect(runtime.disconnect).toHaveBeenCalledTimes(1);
  });

  it('cancelConnect cancels a pending EndCall', async () => {
    vi.useFakeTimers();
    const { connection, runtime } = harness();

    emitCommand(connection, command('EndCall', 'Ending the call.'));
    await connection.cancelConnect();
    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);

    expect(runtime.disconnect).not.toHaveBeenCalled();
  });

  it('reconnect cancels a pending EndCall', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.end.mockRejectedValueOnce(new Error('release failed'));
    const { connection, runtime } = harness();

    emitCommand(connection, command('EndCall', 'Ending the call.'));
    await connection.reconnect();
    expect(runtime.disconnect).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(END_AFTER_SPEECH_CAP_MS);
    expect(runtime.disconnect).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
});
