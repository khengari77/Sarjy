import type { Room } from 'livekit-client';

/** Detach and remove every audio element owned by one room generation. */
export function disposeAudioElements(
  audio: Iterable<HTMLMediaElement>,
  detach: (element: HTMLMediaElement) => void,
): void {
  for (const element of audio) {
    detach(element);
    element.pause();
    element.srcObject = null;
    element.remove();
  }
}

/** Release capture before a potentially slow disconnect. */
export async function releaseMicrophone(room: Room): Promise<void> {
  try {
    await room.localParticipant.setMicrophoneEnabled(false);
  } catch {
    /* the room may already be gone; disconnect() is still required */
  }
}
