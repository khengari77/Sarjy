/**
 * Focus mechanics for the shell's overlay drawers.
 *
 * `inert` on the rest of the shell already removes the background from the
 * tab order and the accessibility tree; these helpers cover what inert alone
 * cannot: choosing the element focus lands on when a drawer opens, and
 * wrapping Tab/Shift+Tab so focus cannot walk out into browser chrome.
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/** Tab-reachable elements inside `root`, in document order. */
export function focusableWithin(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => element.tabIndex >= 0 && element.getClientRects().length > 0 && !element.closest('[inert]'),
  );
}

/** First element a keyboard user can actually reach inside `root`. */
export function firstFocusable(root: HTMLElement | null): HTMLElement | null {
  return focusableWithin(root)[0] ?? null;
}

/**
 * Wraps Tab/Shift+Tab at the drawer boundaries. Call from a keydown handler;
 * returns true when focus was moved (and the event consumed).
 */
export function wrapTab(event: KeyboardEvent, root: HTMLElement | null): boolean {
  if (event.key !== 'Tab') return false;
  const focusables = focusableWithin(root);
  if (!focusables.length) return false;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement as HTMLElement | null;

  if (!active || !root?.contains(active)) {
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
