/**
 * Local audio envelope metering.
 *
 * Only amplitude is measured. Nothing is recorded, stored, or sent anywhere;
 * the analyser lives for the duration of the voice session and is disposed with
 * it (spec sections 4.6 and 10.2).
 */

export interface EnvelopeMeter {
  level(): number;
  dispose(): void;
}

interface MeterOptions {
  smoothing?: number;
  floor?: number;
}

const DEFAULT_SMOOTHING = 0.82;
const DEFAULT_FLOOR = 0.015;

export function createEnvelopeMeter(stream: MediaStream, options: MeterOptions = {}): EnvelopeMeter {
  const AudioContextCtor =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) {
    return { level: () => 0, dispose: () => {} };
  }

  const context = new AudioContextCtor();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.6;
  source.connect(analyser);

  const samples = new Float32Array(analyser.fftSize);
  const smoothing = options.smoothing ?? DEFAULT_SMOOTHING;
  const floor = options.floor ?? DEFAULT_FLOOR;
  let smoothed = 0;

  function level(): number {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (let index = 0; index < samples.length; index += 1) sum += samples[index] * samples[index];
    const rms = Math.sqrt(sum / samples.length);
    const amplitude = Math.min(1, Math.max(0, (rms - floor) * 6));
    smoothed = amplitude > smoothed ? amplitude : smoothed * smoothing + amplitude * (1 - smoothing);
    return Math.min(1, smoothed);
  }

  return {
    level,
    dispose() {
      try {
        source.disconnect();
        analyser.disconnect();
        void context.close();
      } catch {
        /* the context may already be closed during teardown */
      }
    },
  };
}
