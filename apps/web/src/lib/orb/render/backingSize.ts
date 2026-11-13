export interface CssSize {
  width: number;
  height: number;
}

export interface BackingSizer {
  apply(): boolean;
  refreshCss(): boolean;
  dispose(): void;
}

export function createBackingSizer(
  canvas: HTMLCanvasElement,
  getCssSize: () => CssSize,
  getDpr: () => number,
  getMaxDpr: () => number,
): BackingSizer {
  let disposed = false;
  let css = getCssSize();
  let width = 0;
  let height = 0;

  function computed() {
    const ratio = Math.min(getDpr() || 1, getMaxDpr());
    return {
      width: Math.max(1, Math.round(css.width * ratio)),
      height: Math.max(1, Math.round(css.height * ratio)),
    };
  }

  function apply() {
    if (disposed) return false;
    const next = computed();
    if (next.width === width && next.height === height) return false;
    width = next.width;
    height = next.height;
    canvas.width = width;
    canvas.height = height;
    return true;
  }

  function refreshCss() {
    if (disposed) return false;
    css = getCssSize();
    return apply();
  }

  return {
    apply,
    refreshCss,
    dispose() {
      disposed = true;
    },
  };
}
