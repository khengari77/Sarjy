import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VoiceRuntime } from '../audio/voice-runtime';
import { createMeterController } from './meters';

function installAudio(level = 0.25) {
  const contexts: { close: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }[] = [];
  class AudioContext {
    close = vi.fn(async () => {});
    createMediaStreamSource() {
      const disconnect = vi.fn();
      contexts.push({ close: this.close, disconnect });
      return { connect: vi.fn(), disconnect };
    }
    createAnalyser() {
      return {
        fftSize: 512,
        smoothingTimeConstant: 0.6,
        getFloatTimeDomainData: (samples: Float32Array) => samples.fill(level),
        connect: vi.fn(),
        disconnect: vi.fn(),
      };
    }
  }
  vi.stubGlobal('window', { AudioContext });
  return contexts;
}

function fakeStream() {
  const track = { stop: vi.fn() };
  return { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
}

function harness() {
  const levels = { micLevel: 0, outputLevel: 0 };
  let stream: MediaStream | null = null;
  const runtime = {
    microphoneStream: () => stream,
    remoteAudioLevel: () => 0,
  } as unknown as VoiceRuntime;
  let live = true;
  let muted = false;
  const controller = createMeterController(
    levels,
    () => runtime,
    () => live,
    () => muted,
  );
  return {
    levels,
    controller,
    setStream: (next: MediaStream | null) => (stream = next),
    setLive: (next: boolean) => (live = next),
    setMuted: (next: boolean) => (muted = next),
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('microphone meter startup', () => {
  it('retries until the runtime stream exists, then reports a level', () => {
    vi.useFakeTimers();
    installAudio();
    const h = harness();

    h.controller.setupMeters();
    vi.advanceTimersByTime(80);
    expect(h.levels.micLevel).toBe(0);

    h.setStream(fakeStream());
    vi.advanceTimersByTime(80);
    expect(h.levels.micLevel).toBeGreaterThan(0);
  });

  it('goes silent when muted and resumes after unmute', () => {
    vi.useFakeTimers();
    const contexts = installAudio();
    const h = harness();
    h.setStream(fakeStream());
    h.controller.setupMeters();
    vi.advanceTimersByTime(80);
    expect(h.levels.micLevel).toBeGreaterThan(0);

    h.setMuted(true);
    h.controller.stopMicMeter();
    vi.advanceTimersByTime(240);
    expect(h.levels.micLevel).toBe(0);

    h.setMuted(false);
    h.controller.startMicMeter();
    vi.advanceTimersByTime(80);
    expect(h.levels.micLevel).toBeGreaterThan(0);

    // Unmute replaces the envelope instead of stacking a second one.
    expect(contexts.length).toBe(2);
    expect(contexts[0]!.close).toHaveBeenCalledTimes(1);
  });

  it('tears down to silence and disposes the analyser', () => {
    vi.useFakeTimers();
    const contexts = installAudio();
    const h = harness();
    h.setStream(fakeStream());
    h.controller.setupMeters();
    vi.advanceTimersByTime(80);
    expect(h.levels.micLevel).toBeGreaterThan(0);

    h.controller.teardownMeters();
    expect(h.levels.micLevel).toBe(0);
    expect(contexts[0]!.close).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(240);
    expect(h.levels.micLevel).toBe(0);
    expect(contexts.length).toBe(1);
  });

  it('does not attach a meter while the session is not live', () => {
    vi.useFakeTimers();
    const contexts = installAudio();
    const h = harness();
    h.setLive(false);
    h.setStream(fakeStream());
    h.controller.setupMeters();
    vi.advanceTimersByTime(240);
    expect(h.levels.micLevel).toBe(0);
    expect(contexts.length).toBe(0);
  });
});
