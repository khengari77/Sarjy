import { tick } from 'svelte';
import { nextRendererTier, selectRendererTier, type RendererTier } from '../orbState';
import { createBackingSizer, type CssSize } from './backingSize';
import { startCanvasDrawing } from './canvasLoop';
import { hexToRgb } from './colors';
import type { OrbRendererHooks, OrbRenderState, OrbTierState, Rgb } from './types';
import { setupWebgl } from './webgl';

function createSizer(canvas: HTMLCanvasElement) {
  const measure = (): CssSize => {
    const rect = canvas.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  };
  const small = () => window.matchMedia('(max-width: 767px)').matches;
  const sizer = createBackingSizer(
    canvas,
    measure,
    () => window.devicePixelRatio || 1,
    () => (small() ? 1.5 : 2),
  );
  return {
    apply: () => sizer.apply(),
    refreshCss: () => sizer.refreshCss(),
    dispose: () => sizer.dispose(),
  };
}

export function startOrbRenderer(
  canvas: HTMLCanvasElement,
  renderState: OrbRenderState,
  hooks: OrbRendererHooks,
  tierState: OrbTierState,
): () => void {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const probe = document.createElement('canvas');
  const webglAvailable = Boolean(probe.getContext('webgl2'));
  const environment = () => ({ reducedMotion: reduced.matches, webglAvailable });
  const selected = selectRendererTier(environment());
  // A demoted renderer restarts one step down even on a fresh surface.
  let tier: RendererTier = tierState.demoted && selected === 'enhanced' ? 'light' : selected;
  hooks.onTier(tier);

  let disposed = false;
  const currentColor: { rgb: Rgb } = { rgb: hexToRgb(renderState.color) };
  const currentMotion = { ...renderState.motion };
  let fpsSamples = 0;
  let fpsElapsed = 0;
  let stopDrawing: (() => void) | null = null;

  function setTier(next: RendererTier) {
    tier = next;
    hooks.onTier(next);
  }

  /** Downward-only transition: remember it so a remount cannot undo it. */
  function demote(next: RendererTier) {
    tierState.demoted = true;
    setTier(next);
  }

  function monitorFps(delta: number) {
    if (tier === 'static' || disposed || document.hidden) return;
    fpsSamples += 1;
    fpsElapsed += delta;
    if (fpsElapsed < 3000) return;
    const fps = (fpsSamples * 1000) / fpsElapsed;
    fpsSamples = 0;
    fpsElapsed = 0;
    const next = nextRendererTier(tier, fps);
    if (next === tier) return;
    demote(next);
    if (next === 'static') hooks.onFailed();
    void tick().then(() => setup());
  }

  function smoothMotion(delta: number) {
    const ratio = Math.min(1, delta / 320);
    for (const key of ['turbulence', 'flowRate', 'edgeGlow', 'particleActivity', 'opacity'] as const) {
      currentMotion[key] += (renderState.motion[key] - currentMotion[key]) * ratio;
    }
  }

  const sizer = createSizer(canvas);
  const resize = sizer.apply;

  function setup() {
    stopDrawing?.();
    stopDrawing = null;
    if (disposed) return;
    if (tier === 'static') return;

    const gl =
      tier === 'enhanced'
        ? canvas.getContext('webgl2', { alpha: true, antialias: true, premultipliedAlpha: false })
        : null;
    if (gl) {
      const stop = setupWebgl({
        gl,
        canvas,
        renderState,
        currentColor,
        getPulse: hooks.getPulse,
        smoothMotion,
        monitorFps,
        resize,
        isDisposed: () => disposed,
        onFailed: hooks.onFailed,
        onContextLost: () => {
          hooks.onFailed();
          demote('static');
          stopDrawing?.();
        },
      });
      if (stop) {
        stopDrawing = stop;
        return;
      }
      demote('light');
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      demote('static');
      return;
    }
    stopDrawing = startCanvasDrawing({
      canvas,
      renderState,
      currentColor,
      currentMotion,
      getPulse: hooks.getPulse,
      smoothMotion,
      monitorFps,
      resize,
      isDisposed: () => disposed,
    });
  }

  const onMotionChange = () => {
    const resolved = selectRendererTier(environment());
    // Demotion is sticky state, not a tier passed into this constructor: once
    // the ladder has dropped, a preference change cannot climb back to WebGL.
    const next = tierState.demoted && resolved === 'enhanced' ? 'light' : resolved;
    setTier(next);
    void tick().then(() => setup());
  };
  const onResize = () => sizer.refreshCss();
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(onResize);
  observer?.observe(canvas);
  reduced.addEventListener('change', onMotionChange);
  window.addEventListener('resize', onResize);
  void tick().then(() => {
    sizer.refreshCss();
    setup();
  });

  return () => {
    disposed = true;
    observer?.disconnect();
    sizer.dispose();
    reduced.removeEventListener('change', onMotionChange);
    window.removeEventListener('resize', onResize);
    stopDrawing?.();
  };
}
