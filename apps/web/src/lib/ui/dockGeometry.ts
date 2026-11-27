/**
 * Publishes the measured dock/main-column geometry consumed by the floating
 * change toast (`ChangeToast.svelte`).
 *
 * The toast never overlaps the dock: it sits 12px above the dock's top, and is
 * hidden entirely when that leaves less than 40px of usable band.
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
  const centre = columnRect.left + columnRect.width / 2;

  const show = (bottom: number) => {
    root.style.setProperty('--main-column-center', `${centre}px`);
    root.style.setProperty('--toast-top', `${toastTop}px`);
    root.style.setProperty('--toast-bottom', `${bottom}px`);
    root.dataset.toastReady = 'true';
    return true;
  };

  // The dock is entirely below the viewport: sit at the viewport bottom edge.
  if (dockRect.top >= viewportHeight) return show(12);

  // Keep the full 12px gap above the dock; never clamp the toast into it.
  const band = dockRect.top - 12 - toastTop;
  if (band < 40) {
    delete root.dataset.toastReady;
    return false;
  }

  return show(viewportHeight - dockRect.top + 12);
}
