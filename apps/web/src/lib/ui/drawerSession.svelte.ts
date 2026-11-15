import { tick } from 'svelte';
import { firstFocusable, wrapTab } from './drawerFocus';

/**
 * Drawer focus session: the parts of the overlay drawer's modal behaviour that
 * are easier to read and test as units than inline in AppShell.
 *
 * Invariants:
 * - opening an overlay drawer moves focus to its first meaningful control;
 * - a dialog opened from the stage (the end-call confirmation the drawer itself
 *   triggers) takes over modal ownership: the drawer closes without pulling
 *   focus back to its trigger, and focus returns there afterwards only when
 *   nothing else claimed it;
 * - Tab cycles the drawer plus the always-reachable control strip;
 * - Escape belongs to the open dock that holds focus (both docks can be open).
 */

export type Panel = 'left' | 'right';

export interface DrawerSessionOptions {
  /** The modal (below the dock breakpoint) open panel, if any. */
  overlay: () => Panel | null;
  panel: (panel: Panel) => HTMLElement | null;
  trigger: (panel: Panel) => HTMLElement | null;
  /** Always-reachable control strip (call controls, composer). */
  controls: () => HTMLElement | null;
  openState: (panel: Panel) => boolean;
  setOpen: (panel: Panel, open: boolean) => void;
  /** A stage dialog has taken over the modal layer. */
  dialogOpen: () => boolean;
  closeOverlays: () => void;
}

export function createDrawerSession(options: DrawerSessionOptions) {
  let focused: Panel | null = null;
  let handoff: Panel | null = null;

  // Opening an overlay drawer moves focus to its first meaningful control.
  // Docked panels are not dialogs and never steal focus.
  $effect(() => {
    const panel = options.overlay();
    if (panel === focused) return;
    focused = panel;
    if (!panel) return;
    void tick().then(() => firstFocusable(options.panel(panel))?.focus({ preventScroll: true }));
  });

  // Modal handoff. While the stage dialog is open the drawer stays closed so
  // two modals never compete; afterwards, if nothing claimed focus, return it
  // to the trigger of the drawer that handed off.
  $effect(() => {
    if (!options.dialogOpen()) {
      if (!handoff) return;
      const panel = handoff;
      handoff = null;
      void tick().then(() => {
        if (!document.activeElement || document.activeElement === document.body) {
          options.trigger(panel)?.focus({ preventScroll: true });
        }
      });
      return;
    }
    const open = options.overlay();
    if (!open) return;
    handoff = open;
    options.closeOverlays();
  });

  /** Tab wraps around the drawer plus the persistent control strip. */
  function onTab(event: KeyboardEvent): void {
    const panel = options.overlay();
    if (panel) wrapTab(event, [options.panel(panel), options.controls()]);
  }

  /**
   * Breakpoint crossed between overlay drawers and docked panels. Docked
   * panels keep the focused element mounted; collapsing docks deliberately
   * restore focus to the panel's trigger (both docks collapse at phone width,
   * which is the responsive default even when focus is in neither).
   */
  function onBreakpoint(docked: boolean): void {
    const active = document.activeElement as HTMLElement | null;
    const focusIn =
      (['left', 'right'] as const).find((panel) => active && options.panel(panel)?.contains(active)) ?? null;

    if (docked) {
      options.setOpen('left', options.openState('left') || !options.openState('right'));
      return;
    }
    options.setOpen('left', false);
    options.setOpen('right', false);
    void tick().then(() => {
      if (focusIn) options.trigger(focusIn)?.focus({ preventScroll: true });
    });
  }

  /** Escape target for docked panels: the open dock that holds focus. */
  function escapeTarget(): Panel | null {
    const modal = options.overlay();
    if (modal) return modal;
    const active = document.activeElement as HTMLElement | null;
    if (!active) return null;
    for (const panel of ['left', 'right'] as const) {
      if (!options.openState(panel)) continue;
      if (options.panel(panel)?.contains(active) || options.trigger(panel) === active) return panel;
    }
    return null;
  }

  return { onTab, escapeTarget, onBreakpoint };
}
