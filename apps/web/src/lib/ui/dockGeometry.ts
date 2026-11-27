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
  const toastTop = topbarHeight + 8;
  // When the dock scrolls up past the topbar the toast has no room above it;
  // put it at the viewport's bottom edge instead of collapsing the band.
  const dockAboveTopbar = dockRect.top <= topbarHeight;
  const dockTop = Math.min(viewportHeight, Math.max(toastTop, dockRect.top));
  const preferredBottom = dockAboveTopbar ? 8 : Math.max(8, viewportHeight - dockTop + 12);
  // Keep at least 48px between the toast's top and its bottom edge so the band
  // can never collapse to a negative/zero height.
  const maxBottom = Math.max(8, viewportHeight - toastTop - 48);
  const toastBottom = Math.min(preferredBottom, maxBottom);
  root.style.setProperty('--main-column-center', `${columnRect.left + columnRect.width / 2}px`);
  root.style.setProperty('--toast-top', `${toastTop}px`);
  root.style.setProperty('--toast-bottom', `${toastBottom}px`);
  root.dataset.toastReady = 'true';
  return true;
}
