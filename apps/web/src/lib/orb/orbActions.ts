import type { OrbPresentation } from './orbState';

/**
 * The connection operations the orb button can invoke. The page adapts the
 * real VoiceConnection to this surface; tests substitute spies.
 */
export interface OrbActionSurface {
  start(): void;
  cancel(): void;
  /** Toggle the microphone: used for both `mute` and `unmute`. */
  mute(): void;
  interrupt(): void;
  retry(): void;
  reconnect(): void;
  end(): void;
}

/**
 * Activate the orb. The single button performs exactly the action its
 * accessible name advertises, so the name and the behaviour cannot disagree:
 * the name and this dispatch both read `presentation.primaryAction`.
 */
export function activateOrb(presentation: OrbPresentation, surface: OrbActionSurface): void {
  switch (presentation.primaryAction) {
    case 'start':
      surface.start();
      return;
    case 'cancel':
      surface.cancel();
      return;
    case 'mute':
    case 'unmute':
      surface.mute();
      return;
    case 'interrupt':
      surface.interrupt();
      return;
    case 'retry':
      surface.retry();
      return;
    case 'reconnect':
      surface.reconnect();
      return;
    case 'end':
      surface.end();
      return;
    default:
      return;
  }
}
