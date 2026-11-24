/**
 * Focus mechanics for the shell's overlay drawers.
 *
 * `inert` on the rest of the shell already removes the background from the
 * tab order and the accessibility tree; these helpers cover what inert alone
 * cannot: choosing the element focus lands on when a drawer opens, and
 * wrapping Tab/Shift+Tab so focus cannot walk out into browser chrome.
 *
 * The "modal scope" can be more than one root: the open drawer plus the
 * always-reachable control surface (call controls), which #37 requires to stay
 * usable while a drawer is open.
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/** Tab-reachable elements inside `roots`, in document order. */
export function focusableWithin(roots: Array<HTMLElement | null | undefined>): HTMLElement[] {
  const seen = new Set<HTMLElement>();
  const result: HTMLElement[] = [];
  for (const root of roots) {
    if (!root) continue;
    for (const element of root.querySelectorAll<HTMLElement>(FOCUSABLE)) {
      if (seen.has(element)) continue;
      if (element.tabIndex < 0 || element.getClientRects().length === 0 || element.closest('[inert]')) continue;
      seen.add(element);
      result.push(element);
    }
  }
  // The roots can arrive in any order (drawer first, control strip second);
  // Tab must follow the real document order or the wrap boundary is wrong.
  return result.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
}

/** First element a keyboard user can actually reach inside `root`. */
export function firstFocusable(root: HTMLElement | null): HTMLElement | null {
  return focusableWithin([root])[0] ?? null;
}

/**
 * Wraps Tab/Shift+Tab at the modal scope's boundaries. Call from a keydown
 * handler; returns true when focus was moved (and the event consumed).
 */
export function wrapTab(event: KeyboardEvent, roots: Array<HTMLElement | null | undefined>): boolean {
  if (event.key !== 'Tab') return false;
  const focusables = focusableWithin(roots);
  if (!focusables.length) return false;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement as HTMLElement | null;
  const inScope = active !== null && roots.some((root) => root?.contains(active));

  if (!inScope) {
    event.preventDefault();
    first.focus({ preventScroll: true });
    return true;
  }
  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus({ preventScroll: true });
    return true;
  }
  if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus({ preventScroll: true });
    return true;
  }
  return false;
}
