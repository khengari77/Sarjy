import type { SessionConfig } from '../api';
import { endVoiceSession } from '../chat/session';

/**
 * Release a server session exactly once after the backend acknowledges it.
 *
 * A failed release stays retryable: marking it released up front (and
 * swallowing the error) silently leaves admission occupied while End reports
 * idle. Concurrent calls for the same session share one in-flight attempt.
 */
export function createSessionReleaser() {
  const released = new Set<string>();
  const releasing = new Map<string, Promise<boolean>>();

  return async function releaseSession(session: SessionConfig | null): Promise<boolean> {
    if (!session || released.has(session.id)) return true;
    const inFlight = releasing.get(session.id);
    if (inFlight) return inFlight;
    const attempt = (async () => {
      try {
        await endVoiceSession(session);
        released.add(session.id);
        return true;
      } catch {
        return false;
      } finally {
        releasing.delete(session.id);
      }
    })();
    releasing.set(session.id, attempt);
    return attempt;
  };
}
