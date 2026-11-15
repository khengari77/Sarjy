import { tick } from 'svelte';

/**
 * Opens the right drawer on its Transcript tab for a control that lives outside
 * the shell (the caption bubble). The shell returns focus to `opener` when the
 * drawer closes. A docked drawer never steals focus on its own, and this is a
 * keyboard activation, so focus moves into the drawer's selected tab.
 */
export async function revealTranscript(
  opener: HTMLElement,
  panels: { memory_explorer: boolean },
  drawer: { panelTab: string },
  setOpener: (opener: HTMLElement) => void,
): Promise<void> {
  setOpener(opener);
  drawer.panelTab = 'transcript';
  panels.memory_explorer = true;
  await tick();
  document.getElementById('drawer-tab-transcript')?.focus({ preventScroll: true });
}
