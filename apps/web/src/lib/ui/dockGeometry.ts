/**
 * Publishes the measured dock/main-column geometry consumed by the floating
 * change toast (`ChangeToast.svelte`).
 *
 * Returns false, and leaves the toast hidden, when the dock has no rendered
 * box (e.g. `display: none`): such a dock reports top 0, which would place the
 * toast off-screen.
 */
export function publishDockGeometry(
  root: HTMLElement,
  dockRect: DOMRect,
  columnRect: DOMRect,
  topbar: HTMLElement | null,
  viewportHeight: number,
): boolean {
  if (!(dockRect.height > 0 && columnRect.width > 0)) {
    delete root.dataset.toastReady;
    return false;
  }

  const measuredTopbar = topbar?.getBoundingClientRect().height ?? 0;
  const topbarHeight = measuredTopbar > 0 ? measuredTopbar : 68;
  const dockHeight = Math.max(0, Math.min(viewportHeight, viewportHeight - dockRect.top));
  const dockTop = Math.min(viewportHeight, Math.max(topbarHeight + 8, dockRect.top));
  root.style.setProperty('--dock-h', `${dockHeight}px`);
  root.style.setProperty('--main-column-center', `${columnRect.left + columnRect.width / 2}px`);
  root.style.setProperty('--toast-top', `${topbarHeight + 8}px`);
  // Keep the toast above the dock, but never above the topbar: when the dock
  // scrolls below a short viewport, the toast sits at the viewport bottom.
  root.style.setProperty('--toast-bottom', `${Math.max(8, viewportHeight - dockTop + 12)}px`);
  root.dataset.toastReady = 'true';
  return true;
}
