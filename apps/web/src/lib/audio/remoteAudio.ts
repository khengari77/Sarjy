import { disposeAudioResources } from './disposeAudio';

interface AnalyserEntry {
  source: MediaElementAudioSourceNode;
  analyser: AnalyserNode;
  data: Float32Array;
}

/** Cosmetic remote-audio analysis kept out of the voice runtime's lifecycle code. */
export class RemoteAudioAnalysers {
  private context: AudioContext | null = null;
  private entries = new Map<HTMLMediaElement, AnalyserEntry>();

  attach(element: HTMLMediaElement) {
    try {
      const AudioCtor =
        window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtor) return;
      this.context ??= new AudioCtor();
      const source = this.context.createMediaElementSource(element);
      const analyser = this.context.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyser.connect(this.context.destination);
      this.entries.set(element, { source, analyser, data: new Float32Array(analyser.fftSize) });
    } catch {
      // Analysis is cosmetic: never let it stop remote audio playback.
    }
  }

  detach(element: HTMLMediaElement) {
    const entry = this.entries.get(element);
    if (!entry) return;
    try {
      entry.source.disconnect();
      entry.analyser.disconnect();
    } catch {
      /* already disconnected */
    }
    this.entries.delete(element);
  }

  /** Smoothed agent-output level for the orb; 0 when audio analysis is unavailable. */
  level(): number {
    if (!this.entries.size) return 0;
    let total = 0;
    for (const { analyser, data } of this.entries.values()) {
      analyser.getFloatTimeDomainData(data as Float32Array<ArrayBuffer>);
      let sum = 0;
      for (const sample of data) sum += sample * sample;
      total += Math.sqrt(sum / data.length);
    }
    return Math.min(1, (total / this.entries.size) * 4);
  }

  /**
   * Stop one captured generation. Each connection owns its own instance, so a
   * replacement call's analysers/context are never touched here.
   */
  dispose(audio: Set<HTMLMediaElement>) {
    const context = this.context;
    this.context = null;
    disposeAudioResources(audio, this.entries, context);
  }
}
