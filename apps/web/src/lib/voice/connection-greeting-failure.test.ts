import { afterEach, describe, expect, it, vi } from 'vitest';
import { friendlyVoiceError } from '../audio/livekitAdapter';
import { config, deferred, full, harness } from './greetingHarness';

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

describe('greeting failure', () => {
  it('a failed greeting starts the mic immediately and never marks the intro', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalled());
    player.finish('failed');
    await vi.waitFor(() => expect(runtime.micStarts).toBe(1));
    await connecting;

    expect(state.greeting).toBeNull();
    expect(state.muted).toBe(false);
    expect(mocks.mark).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
});

describe('stop paths and interruption', () => {
  it('End stops the greeting without starting the mic or marking the intro', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(state.greeting).not.toBeNull());
    await connection.end();
    await connecting;

    expect(player.stop).toHaveBeenCalled();
    expect(runtime.micStarts).toBe(0);
    expect(state.greeting).toBeNull();
    expect(mocks.mark).not.toHaveBeenCalled();
  });

  it('cancelConnect stops a playing greeting and supersedes the attempt', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(state.live).toEqual(full));
    await connection.cancelConnect();
    await connecting;

    expect(player.stop).toHaveBeenCalled();
    expect(runtime.micStarts).toBe(0);
    expect(state.greeting).toBeNull();
    expect(mocks.mark).not.toHaveBeenCalled();
  });

  it('interrupt during the greeting skips it and opens the microphone', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(state.live).toEqual(full));
    expect(player.playing).toBe(true);

    await connection.interrupt();

    await vi.waitFor(() => expect(runtime.micStarts).toBe(1));
    expect(player.stop).toHaveBeenCalled();
    expect(state.greeting).toBeNull();
    expect(runtime.interrupts).toBe(1);
    expect(mocks.mark).not.toHaveBeenCalled();
    await connecting;
  });
});

describe('startup failure after the greeting', () => {
  it('keeps the ordinary startup error when the clip is cut off mid-way', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();
    const gate = deferred();
    runtime.connectGate = gate.promise;

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalled());
    gate.reject(new Error('AGENT_START_TIMEOUT'));
    await connecting;

    expect(state.voiceErrorCode).toBe('AGENT_START_TIMEOUT');
    expect(player.stop).toHaveBeenCalled();
    expect(mocks.mark).not.toHaveBeenCalled();
  });

  it('uses the distinct code and copy when the call fails after the clip ended', async () => {
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();
    const gate = deferred();
    runtime.connectGate = gate.promise;

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalled());
    player.finish('ended');
    await vi.waitFor(() => expect(state.greeting).toBeNull());
    gate.reject(new Error('AGENT_START_TIMEOUT'));
    await connecting;

    expect(state.voiceErrorCode).toBe('GREETING_UNCONNECTED');
    expect(mocks.mark).not.toHaveBeenCalled();
    expect(friendlyVoiceError('GREETING_UNCONNECTED', false)).toBe(
      'You heard the introduction, but the call did not connect. Try again.',
    );
  });

  it('keeps the ordinary startup error when the greeting never played', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.start.mockResolvedValueOnce(full);
    const { state, runtime, player, connection } = harness();
    const gate = deferred();
    runtime.connectGate = gate.promise;

    const connecting = connection.connect(true);
    await vi.waitFor(() => expect(player.play).toHaveBeenCalled());
    player.finish('failed');
    gate.reject(new Error('AGENT_START_TIMEOUT'));
    await connecting;

    expect(state.voiceErrorCode).toBe('AGENT_START_TIMEOUT');
    expect(mocks.mark).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it('keeps the ordinary startup error when there is no greeting', async () => {
    mocks.start.mockResolvedValueOnce(config);
    const { state, runtime, player, connection } = harness();
    const gate = deferred();
    runtime.connectGate = gate.promise;

    const connecting = connection.connect(false);
    await vi.waitFor(() => expect(mocks.start).toHaveBeenCalled());
    gate.reject(new Error('AGENT_START_TIMEOUT'));
    await connecting;

    expect(player.play).not.toHaveBeenCalled();
    expect(state.voiceErrorCode).toBe('AGENT_START_TIMEOUT');
    expect(mocks.mark).not.toHaveBeenCalled();
  });
});
