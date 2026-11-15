import { createEnvelopeMeter, type EnvelopeMeter } from '../orb/audioEnvelope';
import type { VoiceRuntime } from '../audio/voice-runtime';

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
    stopMicMeter();
    if (!getLive() || getMuted()) return;
    const runtime = getRuntime();
    const stream = runtime?.microphoneStream?.();
    if (!stream) return;
    micMeter = createEnvelopeMeter(stream);
  }

  function stopOutputMeter() {
    if (meterTimer) clearInterval(meterTimer);
    meterTimer = null;
    levels.outputLevel = 0;
  }

  function startOutputMeter() {
    if (meterTimer) return;
    meterTimer = setInterval(() => {
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
