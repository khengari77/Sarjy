import type { VoiceRuntime } from '../audio/voice-runtime';
import { createEnvelopeMeter, type EnvelopeMeter } from '../orb/audioEnvelope';

export interface LevelState {
  micLevel: number;
  outputLevel: number;
}

export interface MeterController {
  startMicMeter(): void;
  stopMicMeter(): void;
  startOutputMeter(): void;
  stopOutputMeter(): void;
  setupMeters(): void;
  teardownMeters(): void;
}

export function createMeterController(
  levels: LevelState,
  getRuntime: () => VoiceRuntime | undefined,
  getLive: () => boolean,
  getMuted: () => boolean,
): MeterController {
  let micMeter: EnvelopeMeter | null = null;
  let meterTimer: ReturnType<typeof setInterval> | null = null;

  function stopMicMeter() {
    micMeter?.dispose();
    micMeter = null;
    levels.micLevel = 0;
  }

  function startMicMeter() {
    // Replacing a meter must release the previous analyser: one envelope per
    // track, and the current track may have changed after unmute.
    stopMicMeter();
    if (!getLive() || getMuted()) return;
    const stream = getRuntime()?.microphoneStream?.();
    if (!stream) return;
    micMeter = createEnvelopeMeter(stream);
  }

  /**
   * LiveKit publishes the local track asynchronously, so the first attempt
   * after `startMicrophone()` can see no stream. Retry from the level timer
   * until it exists; this is a fallback, not a second capture.
   */
  function ensureMicMeter() {
    if (!getLive() || getMuted()) {
      if (micMeter) stopMicMeter();
      return;
    }
    if (micMeter) return;
    const stream = getRuntime()?.microphoneStream?.();
    if (stream) micMeter = createEnvelopeMeter(stream);
  }

  function stopOutputMeter() {
    if (meterTimer) clearInterval(meterTimer);
    meterTimer = null;
    levels.outputLevel = 0;
  }

  function startOutputMeter() {
    if (meterTimer) return;
    meterTimer = setInterval(() => {
      ensureMicMeter();
      levels.micLevel = micMeter?.level() ?? 0;
      levels.outputLevel = getRuntime()?.remoteAudioLevel?.() ?? 0;
    }, 80);
  }

  function teardownMeters() {
    stopMicMeter();
    stopOutputMeter();
  }

  function setupMeters() {
    startMicMeter();
    startOutputMeter();
  }

  return { startMicMeter, stopMicMeter, startOutputMeter, stopOutputMeter, setupMeters, teardownMeters };
}
