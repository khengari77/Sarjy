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
  // Unit tests and SSR can reach this module without a DOM; a silent meter is
  // the honest answer where there is no Web Audio API at all.
  const scope = typeof window === 'undefined' ? undefined : window;
  const AudioContextCtor =
    scope?.AudioContext ??
    (scope as unknown as { webkitAudioContext?: typeof AudioContext } | undefined)?.webkitAudioContext;
  if (!AudioContextCtor) {
    return { level: () => 0, dispose: () => {} };
  }
  // A preflight/test stream may carry no audio track; there is nothing to
  // analyse and constructing a context would only add noise to teardown.
  const audioTracks = stream.getAudioTracks?.() ?? stream.getTracks?.() ?? [];
  if (audioTracks.length === 0) {
    return { level: () => 0, dispose: () => {} };
  }

  const context = new AudioContextCtor();
  let source: MediaStreamAudioSourceNode;
  let analyser: AnalyserNode;
  try {
    source = context.createMediaStreamSource(stream);
    analyser = context.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    source.connect(analyser);
  } catch {
    // Analysis is cosmetic: an unsupported stream or a stubbed/partial Web
    // Audio implementation must not turn a granted microphone into an error.
    void context.close().catch(() => {});
    return { level: () => 0, dispose: () => {} };
  }

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
