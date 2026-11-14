/**
 * Connecting-phase microphone preview.
 *
 * The level shown while the session is still connecting comes from the same
 * `getUserMedia` capture the connection preflight already requests — the orb
 * must react to a real voice immediately after the user taps, but must never
 * ask for the microphone before that tap.
 *
 * The capture is short-lived. It is released on cancel, permission/startup
 * error, unmount, or the moment the session becomes live. LiveKit publishes its
 * own device track when `startMicrophone()` runs, so the preview is stopped
 * before that call; keeping our probe stream alongside the published track
 * would leave two live captures for one conversation.
 */

import { acquireMicrophone } from '../audio/microphone';
import { createEnvelopeMeter } from '../orb/audioEnvelope';

export interface ConnectingPreview {
  /** Normalised 0..1 microphone envelope; 0 once stopped. */
  level(): number;
  /** Release the analyser and the capture. Idempotent. */
  stop(): void;
}

export async function openConnectingPreview(): Promise<ConnectingPreview> {
  const capture = await acquireMicrophone();
  const meter = createEnvelopeMeter(capture.stream);
  let stopped = false;
  return {
    level: () => (stopped ? 0 : meter.level()),
    stop() {
      if (stopped) return;
      stopped = true;
      meter.dispose();
      capture.stop();
    },
  };
}
