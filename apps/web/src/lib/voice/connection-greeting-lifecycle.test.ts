import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import type { VoiceRuntime } from '../audio/voice-runtime';
import { createVoiceConnection, initialVoiceState } from './connection';
import { allowMicrophone, config, deferred, FakeRuntime, fakePlayer, full, harness } from './greetingHarness';
import { GREETING_MAX_WAIT_MS } from './sessionAttempt';

const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  mark: vi.fn(async () => {}),
  end: vi.fn(async () => {}),
}));

vi.mock('../chat/session', () => ({
  startVoiceSession: mocks.start,
  markIntroComplete: mocks.mark,
  endVoiceSession: mocks.end,
}));

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('call lifecycle during the greeting', () => {
  it('End during the clip lets the next connect start a new session', async () => {
    const second: SessionConfig = { ...config, id: 'sess-2' };
    mocks.start.mockResolvedValueOnce(full).mockResolvedValueOnce(second);
    const { state, connection } = harness();

    const first = connection.connect(true);
    await vi.waitFor(() => expect(state.greeting).not.toBeNull());
    await connection.end();
    await first;
    expect(state.connecting).toBe(false);

    await connection.connect(true);
    expect(state.live?.id).toBe('sess-2');
    expect(mocks.start).toHaveBeenLastCalledWith('c1', 'en', true);
  });

  it('a transport drop during the clip stops it, clears the caption and never marks', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(state.live).toEqual(full));
    connection.handleEvent({ type: 'status', source: 'transport', text: 'disconnected', epoch: 1 });
    expect(player.stop).toHaveBeenCalled();
    expect(state.greeting).toBeNull();

    player.finish('ended');
    await connecting;

    expect(runtime.micStarts).toBe(0);
    expect(mocks.mark).not.toHaveBeenCalled();
  });

  it('a microphone failure after the clip keeps the microphone code', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();
    runtime.micError = new Error('MICROPHONE_PERMISSION_DENIED');

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalled());
    player.finish('ended');
    await connecting;

    expect(state.voiceErrorCode).toBe('MICROPHONE_PERMISSION_DENIED');
    expect(state.voiceErrorCode).not.toBe('GREETING_UNCONNECTED');
    expect(mocks.mark).not.toHaveBeenCalled();
  });

  it('a deliberate End during the clip does not warn about a failed greeting', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.start.mockResolvedValueOnce(full);
    const { state, connection } = harness();

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(state.greeting).not.toBeNull());
    await connection.end();
    await connecting;
    await vi.waitFor(() => expect(vi.mocked(console.warn)).not.toHaveBeenCalled());
    warn.mockRestore();
  });
});

describe('text path', () => {
  it('connect(false) uses worker delivery and never plays a clip', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, player, connection } = harness();

    await connection.connect(false);

    expect(mocks.start).toHaveBeenCalledWith('c1', 'en', false);
    expect(player.play).not.toHaveBeenCalled();
    expect(state.greeting).toBeNull();
  });

  it('sending text while the greeting plays skips it and opens the microphone', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(state.live).toEqual(full));
    expect(player.playing).toBe(true);

    await connection.sendText('hello');

    await vi.waitFor(() => expect(runtime.micStarts).toBe(1));
    expect(player.stop).toHaveBeenCalled();
    expect(state.greeting).toBeNull();
    expect(runtime.texts).toEqual(['hello']);
    await connecting;
  });
});

describe('watchdog ownership and anchoring', () => {
  it('a superseded attempt watchdog cannot stop the newer clip', async () => {
    vi.useFakeTimers();
    const resolvers: ((outcome: 'ended' | 'failed') => void)[] = [];
    const player = {
      prepare: vi.fn(),
      play: vi.fn(() => new Promise<'ended' | 'failed'>((resolve) => resolvers.push(resolve))),
      stop: vi.fn(),
      get playing() {
        return resolvers.length > 0;
      },
    };
    const second: SessionConfig = { ...full, id: 'sess-2' };
    mocks.start.mockResolvedValueOnce(full).mockResolvedValueOnce(second);
    const { state, runtime, connection } = harness(player);

    const first = connection.connect(true);
    await vi.advanceTimersByTimeAsync(0);
    await connection.cancelConnect();
    player.stop.mockClear();

    const gate = deferred();
    runtime.connectGate = gate.promise;
    const replacement = connection.connect(true);
    for (let tick = 0; tick < 20 && player.play.mock.calls.length < 2; tick += 1) {
      await vi.advanceTimersByTimeAsync(1);
    }
    expect(player.play).toHaveBeenCalledTimes(2);
    const newerAttempt = state.greeting?.attempt;

    await vi.advanceTimersByTimeAsync(GREETING_MAX_WAIT_MS);
    expect(player.stop).not.toHaveBeenCalled();
    expect(state.greeting?.attempt).toBe(newerAttempt);

    gate.resolve();
    await vi.advanceTimersByTimeAsync(0);
    resolvers.at(-1)?.('ended');
    await replacement;
    await first;
    await connection.end();
  });

  it('the watchdog cap starts when the room goes live, not when play starts', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();
    const gate = deferred();
    runtime.connectGate = gate.promise;

    const connecting = connection.connect(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(player.play).toHaveBeenCalled();

    setTimeout(() => gate.resolve(), 40_000);
    await vi.advanceTimersByTimeAsync(40_000);
    expect(state.live).toEqual(full);
    expect(runtime.micStarts).toBe(0);

    await vi.advanceTimersByTimeAsync(GREETING_MAX_WAIT_MS - 1);
    expect(runtime.micStarts).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(runtime.micStarts).toBe(1);
    await connecting;
  });
});

describe('prepare ordering', () => {
  it('unlocks the player before the first awaited dependency in connect(true)', async () => {
    vi.useFakeTimers();
    allowMicrophone();
    const order: string[] = [];
    const player = fakePlayer();
    player.prepare.mockImplementation(() => order.push('prepare'));
    const runtime = new FakeRuntime();
    mocks.start.mockResolvedValueOnce(config);
    const connection = createVoiceConnection(initialVoiceState(), {
      ensureConversation: async () => {
        order.push('ensure');
        return 'c1';
      },
      getConversationId: () => null,
      refresh: async () => {},
      getRuntime: () => runtime as unknown as VoiceRuntime,
      getLanguage: () => 'en',
      getVoice: () => ({ id: 'v1', name: 'Voice' }),
      applyCommand: () => {},
      resyncSettings: () => {},
      meters: {
        startMicMeter: vi.fn(),
        stopMicMeter: vi.fn(),
        startOutputMeter: vi.fn(),
        stopOutputMeter: vi.fn(),
        setupMeters: vi.fn(),
        teardownMeters: vi.fn(),
      },
      greetingPlayer: player,
    });

    const connecting = connection.connect(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(order.slice(0, 2)).toEqual(['prepare', 'ensure']);
    await connecting;
  });
});
