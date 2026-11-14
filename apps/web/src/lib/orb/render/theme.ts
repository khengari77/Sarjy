import { ORB_PALETTES, type ThemeName } from '../palette';

/**
 * Resolve the renderer theme from the stamped document attribute.
 *
 * `light` is the safe default: unknown values never leak into GLSL.
 */
export function resolveTheme(value: string | undefined): ThemeName {
  return value === 'dark' || value === 'light' ? value : 'light';
}

export function currentTheme(): ThemeName {
  if (typeof document === 'undefined') return 'light';
  const stamped = document.documentElement.dataset.theme;
  if (stamped === 'light' || stamped === 'dark') return stamped;
  // Before #37 stamps data-theme, tokens.css serves dark by default and only
  // overrides to light for a light system preference.
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  return 'light';
}

export function paletteFor(theme: ThemeName): readonly string[] {
  return ORB_PALETTES[theme];
}

/**
 * Watch `data-theme` and report only real theme changes. Returns an idempotent
 * disposer; no observer is registered outside the browser.
 */
export function watchTheme(onChange: (theme: ThemeName) => void): () => void {
  if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return () => {};
  let last = currentTheme();
  const observer = new MutationObserver(() => {
    const next = currentTheme();
    if (next === last) return;
    last = next;
    onChange(next);
  });
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  return () => observer.disconnect();
}
