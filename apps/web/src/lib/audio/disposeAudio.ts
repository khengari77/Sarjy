interface AudioNodes {
  source: { disconnect(): void };
  analyser: { disconnect(): void };
}

function attempt(cleanup: () => void) {
  try {
    cleanup();
  } catch {
    console.debug('[Sarjy] Local audio resource disposal failed');
  }
}

export function disposeAudioResources(
  audio: Set<HTMLMediaElement>,
  analysers: Map<HTMLMediaElement, AudioNodes>,
  context: AudioContext | null,
): void {
  for (const element of audio) {
    const entry = analysers.get(element);
    attempt(() => entry?.source.disconnect());
    attempt(() => entry?.analyser.disconnect());
    attempt(() => element.pause());
    attempt(() => {
      element.srcObject = null;
    });
    attempt(() => element.remove());
  }
  audio.clear();
  analysers.clear();
  // Playback is stopped above. Browser context closure must not gate teardown.
  attempt(() => {
    void context?.close().catch(() => console.debug('[Sarjy] Audio context closure failed'));
  });
}
