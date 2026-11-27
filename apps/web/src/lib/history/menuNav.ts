/** Focus movement for the row's WAI-ARIA menu button. */
const NAV_KEYS = ['ArrowDown', 'ArrowUp', 'Home', 'End'];

function enabledItems(menu: HTMLElement | null): HTMLButtonElement[] {
  return Array.from(menu?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') ?? []);
}

/** Focus the first or last enabled menu item after the menu mounts. */
export function focusMenuItem(menu: HTMLElement | null, last = false): void {
  const items = enabledItems(menu);
  (last ? items.at(-1) : items[0])?.focus();
}

/**
 * Move focus for ArrowDown/ArrowUp/Home/End, wrapping at the ends. Returns
 * true when the key was consumed, so the caller can prevent scrolling.
 */
export function moveMenuFocus(menu: HTMLElement | null, key: string): boolean {
  if (!NAV_KEYS.includes(key)) return false;
  const items = enabledItems(menu);
  if (!items.length) return false;
  const current = items.indexOf(document.activeElement as HTMLButtonElement);
  let next = 0;
  if (key === 'End') next = items.length - 1;
  else if (key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % items.length;
  else if (key === 'ArrowUp') next = current <= 0 ? items.length - 1 : current - 1;
  items[next]?.focus();
  return true;
}
