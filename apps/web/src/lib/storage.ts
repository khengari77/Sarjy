/**
 * Browser storage access that cannot break the application.
 *
 * Reading `window.localStorage` itself can throw (privacy modes, blocked
 * third-party storage, enterprise policy). That throw happens on the property
 * access, before any caller's try/catch around `getItem`/`setItem` can run, so
 * the property access has to live inside this guard.
 */
export function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
