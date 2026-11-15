import type { SessionConfig } from '../api';
import { endVoiceSession } from '../chat/session';

/** Completed releases remembered before the oldest is forgotten again. */
const RELEASED_MEMORY = 32;

/**
 * Release a server session after the backend acknowledges it.
 *
 * Successful ids are remembered (bounded, oldest evicted) so a repeat call is a
 * no-op while its id is still remembered; an evicted id can be released again,
 * and the server release is expected to be idempotent. A failed release is
 * never marked released and stays retryable. Concurrent calls for the same
 * session share one in-flight attempt. Failures are reported with the session
 * id and the failure name so an occupied admission can be diagnosed and
 * retried.
 */
export function createSessionReleaser() {
  const released = new Set<string>();
  const releasing = new Map<string, Promise<boolean>>();

  function remember(sessionId: string) {
    released.add(sessionId);
    // Bounded: a long-lived tab must not retain every session id it ended.
    while (released.size > RELEASED_MEMORY) {
      const oldest = released.values().next().value;
      if (oldest === undefined) break;
      released.delete(oldest);
    }
  }

  return async function releaseSession(session: SessionConfig | null): Promise<boolean> {
    if (!session || released.has(session.id)) return true;
    const inFlight = releasing.get(session.id);
    if (inFlight) return inFlight;
    const attempt = (async () => {
      try {
        await endVoiceSession(session);
        remember(session.id);
        return true;
      } catch (error) {
        console.warn('[Sarjy] Voice session release failed', {
          sessionId: session.id,
          reason: error instanceof Error ? error.name : 'unknown',
        });
        return false;
      } finally {
        releasing.delete(session.id);
      }
    })();
    releasing.set(session.id, attempt);
    return attempt;
  };
}
