import type { SessionConfig } from '../api';
import type { VoiceState } from './connection';

/**
 * Record a failed session release. A replacement that is live or still
 * connecting keeps ownership; otherwise the session stays live so End can
 * retry it instead of reporting a clean idle state while admission is held.
 */
export function applyFailedRelease(state: VoiceState, session: SessionConfig, replacing: boolean) {
  if (replacing) {
    console.warn('[Sarjy] Voice session release failed; replacement kept live', { sessionId: session.id });
    return;
  }
  state.live = session;
  state.muted = true;
  state.voiceErrorCode = 'SESSION_RELEASE_FAILED';
  state.transportStatus = 'disconnected';
}
