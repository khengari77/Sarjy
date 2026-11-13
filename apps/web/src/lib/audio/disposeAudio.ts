interface AudioNodes {
  source: { disconnect(): void };
  analyser: { disconnect(): void };
}

function attempt(cleanup: () => void) {
  try {
    cleanup();
  } catch {
    /* Continue releasing other resources. */
  }
}

export async function disposeAudioResources(
  audio: Set<HTMLMediaElement>,
  analysers: Map<HTMLMediaElement, AudioNodes>,
  context: AudioContext | null,
): Promise<void> {
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
  if (context) await context.close().catch(() => {});
}
