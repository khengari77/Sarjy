import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import { config, deferred, full, harness, short } from './greetingHarness';
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

describe('browser greeting start', () => {
  it('starts only after the session response', async () => {
    const session = deferred<SessionConfig>();
    mocks.start.mockReturnValueOnce(session.promise);
    const { connection, player } = harness();

    const connecting = connection.connect(false);
    await vi.waitFor(() => expect(mocks.start).toHaveBeenCalled());
    expect(player.play).not.toHaveBeenCalled();

    session.resolve(full);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalledWith('/greetings/voice-1/full.mp3'));
    player.finish('ended');
    await connecting;
  });

  it('prepares the player inside connect before the first await', async () => {
    mocks.start.mockResolvedValueOnce(config);
    const { connection, player } = harness();
    await connection.connect(false);
    expect(player.prepare).toHaveBeenCalledOnce();
  });
});

describe('microphone waits for the greeting', () => {
  it('does not start the mic while the call is live and the greeting plays', async () => {
    const { state, runtime, player, connection } = harness();
    mocks.start.mockResolvedValueOnce(full);

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(state.live).toEqual(full));
    expect(player.play).toHaveBeenCalledWith('/greetings/voice-1/full.mp3');
    expect(runtime.micStarts).toBe(0);
    expect(state.muted).toBe(true);
    expect(state.greeting).toMatchObject({ text: 'The full introduction.', kind: 'full' });

    player.finish('ended');
    await vi.waitFor(() => expect(runtime.micStarts).toBe(1));
    expect(state.muted).toBe(false);
    expect(state.greeting).toBeNull();
    await vi.waitFor(() => expect(mocks.mark).toHaveBeenCalledOnce());
    expect(mocks.mark).toHaveBeenCalledWith('sess-1');
    await connecting;
  });
});

describe('greeting watchdog', () => {
  it('a clip that never ends cannot block the microphone past the cap', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();

    const connecting = connection.connect(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(player.play).toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(GREETING_MAX_WAIT_MS - 1);
    expect(runtime.micStarts).toBe(0);
    expect(player.playing).toBe(true);

    await vi.advanceTimersByTimeAsync(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(runtime.micStarts).toBe(1);
    expect(player.stop).toHaveBeenCalled();
    expect(state.greeting).toBeNull();
    expect(mocks.mark).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledTimes(1);
    await connecting;
    warn.mockRestore();
  });

  it('a clip that ends 18 s after play is unaffected when the call went live at 12 s', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();
    const gate = deferred();
    runtime.connectGate = gate.promise;

    const connecting = connection.connect(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(player.play).toHaveBeenCalled();

    setTimeout(() => gate.resolve(), 12_000);
    await vi.advanceTimersByTimeAsync(12_000);
    expect(state.live).toEqual(full);
    expect(runtime.micStarts).toBe(0);

    setTimeout(() => player.finish('ended'), 6_000);
    await vi.advanceTimersByTimeAsync(6_000);
    await vi.advanceTimersByTimeAsync(0);

    expect(runtime.micStarts).toBe(1);
    expect(mocks.mark).toHaveBeenCalledOnce();
    expect(player.stop).not.toHaveBeenCalled();
    await connecting;
  });
});

describe('intro completion', () => {
  it('never marks a short greeting', async () => {
    mocks.start.mockResolvedValueOnce(short);
    const { player, connection } = harness();

    const connecting = connection.connect(false);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalledWith('/greetings/voice-1/short.mp3'));
    player.finish('ended');
    await connecting;

    expect(mocks.mark).not.toHaveBeenCalled();
  });

  it('marks a full greeting once even when it ends before the call is live', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();
    const gate = deferred();
    runtime.connectGate = gate.promise;

    const connecting = connection.connect(false);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalled());
    player.finish('ended');
    await vi.waitFor(() => expect(state.greeting).toBeNull());
    expect(mocks.mark).not.toHaveBeenCalled();

    gate.resolve();
    await connecting;

    expect(state.live).toEqual(full);
    expect(mocks.mark).toHaveBeenCalledOnce();
    expect(mocks.mark).toHaveBeenCalledWith('sess-1');
  });

  it('marks a full greeting once after both ended and live', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, player, connection } = harness();

    const connecting = connection.connect(false);
    await vi.waitFor(() => expect(state.live).toEqual(full));
    expect(mocks.mark).not.toHaveBeenCalled();
    player.finish('ended');
    await connecting;

    expect(mocks.mark).toHaveBeenCalledOnce();
  });
});

describe('no intro keeps the worker greeting', () => {
  it('plays nothing and starts the microphone as before', async () => {
    mocks.start.mockResolvedValueOnce(config);
    const { state, runtime, player, connection } = harness();

    await connection.connect(true);

    expect(player.play).not.toHaveBeenCalled();
    expect(runtime.micStarts).toBe(1);
    expect(state.greeting).toBeNull();
    expect(mocks.mark).not.toHaveBeenCalled();
  });
});
