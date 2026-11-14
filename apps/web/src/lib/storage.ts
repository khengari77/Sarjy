/**
 * Browser storage access that cannot break the application.
 *
 * Two failure modes exist and both must be contained:
 * - reading `window.localStorage` itself can throw (privacy modes, blocked
 *   third-party storage, enterprise policy) — the throw happens on the
 *   property access, before any caller's try/catch around `getItem` can run;
 * - the returned storage object's methods can throw too (`SecurityError` on
 *   `getItem`, `QuotaExceededError` on `setItem`).
 *
 * So this returns a wrapper whose operations are each guarded. Callers can use
 * it anywhere and never need their own try/catch.
 */

export interface BrowserStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function browserStorage(): BrowserStorage {
  let native: Storage | null = null;
  try {
    native = window.localStorage;
  } catch {
    native = null;
  }

  return {
    getItem(key) {
      try {
        return native?.getItem(key) ?? null;
      } catch {
        return null;
      }
    },
    setItem(key, value) {
      try {
        native?.setItem(key, value);
      } catch {
        // Quota/private-mode failures are presentation-only: the in-memory
        // preference still applies for this page view.
      }
    },
    removeItem(key) {
      try {
        native?.removeItem(key);
      } catch {
        // Same as setItem: dropping a cosmetic preference is best effort.
      }
    },
  };
}
