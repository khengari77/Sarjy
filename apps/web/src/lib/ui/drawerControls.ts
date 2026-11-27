import { tick } from 'svelte';

/**
 * Close an overlay drawer and return focus to its header toggle (or the
 * control that opened it). Focus waits for the shell's `inert` to lift.
 */
export function closeDrawer(
  id: 'shell-left' | 'shell-right',
  setOpen: (open: boolean) => void,
  opener: () => HTMLElement | null = () => null,
  clearOpener: () => void = () => {},
): () => Promise<void> {
  return async () => {
    const target = opener();
    clearOpener();
    setOpen(false);
    await tick();
    (target ?? document.querySelector<HTMLButtonElement>(`[aria-controls="${id}"]`))?.focus({ preventScroll: true });
  };
}
