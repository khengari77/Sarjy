import { tick } from 'svelte';
import { nextRendererTier, selectRendererTier, type RendererTier } from '../orbState';
import { buildParticles, hexToRgb } from './particles';
import type { OrbRendererHooks, OrbRenderState, Rgb } from './types';
import { setupWebgl } from './webgl';

const CLOUD_RADIUS = 0.74;

export function startOrbRenderer(
  canvas: HTMLCanvasElement,
  renderState: OrbRenderState,
  hooks: OrbRendererHooks,
): () => void {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const probe = document.createElement('canvas');
  const webglAvailable = Boolean(probe.getContext('webgl2'));
  let tier: RendererTier = selectRendererTier({ reducedMotion: reduced.matches, webglAvailable });
  hooks.onTier(tier);

  let disposed = false;
  const start = performance.now();
  let last = start;
  const currentColor: { rgb: Rgb } = { rgb: hexToRgb(renderState.color) };
  const currentMotion = { ...renderState.motion };
  let fpsSamples = 0;
  let fpsElapsed = 0;
  let stopDrawing: (() => void) | null = null;

  function setTier(next: RendererTier) {
    tier = next;
    hooks.onTier(next);
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
    setTier(next);
    if (next === 'static') hooks.onFailed();
    void tick().then(() => setup());
  }

  function smoothMotion(delta: number) {
    const ratio = Math.min(1, delta / 320);
    for (const key of ['turbulence', 'flowRate', 'edgeGlow', 'particleActivity', 'opacity'] as const) {
      currentMotion[key] += (renderState.motion[key] - currentMotion[key]) * ratio;
    }
  }

  function resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, isSmallViewport() ? 1.5 : 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * ratio));
    canvas.height = Math.max(1, Math.round(rect.height * ratio));
  }

  function isSmallViewport() {
    return window.matchMedia('(max-width: 767px)').matches;
  }

  function setup() {
    stopDrawing?.();
    stopDrawing = null;
    if (disposed) return;
    if (tier === 'static') return;

    const gl = tier === 'enhanced' ? canvas.getContext('webgl2', { alpha: true, antialias: true }) : null;
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
          setTier('static');
          stopDrawing?.();
        },
      });
      if (stop) {
        stopDrawing = stop;
        return;
      }
      setTier('light');
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      setTier('static');
      return;
    }
    stopDrawing = setupCanvas(ctx);
  }

  function setupCanvas(ctx: CanvasRenderingContext2D): () => void {
    const count = 420;
    const { positions, seeds } = buildParticles(count);
    let angle = 0;
    let canvasFrame = 0;

    function draw(now: number) {
      if (disposed) return;
      canvasFrame = requestAnimationFrame(draw);
      if (document.hidden) return;
      const delta = Math.min(64, now - last);
      last = now;
      const time = (now - start) / 1000;
      const pulse = hooks.getPulse();
      smoothMotion(delta);
      monitorFps(delta);
      const motion = currentMotion;
      angle += (delta / 1000) * (0.12 + motion.flowRate * 0.35 + pulse * 0.2);

      resize();
      const width = canvas.width;
      const height = canvas.height;
      ctx.clearRect(0, 0, width, height);
      ctx.globalCompositeOperation = 'lighter';

      const target = hexToRgb(renderState.color);
      const ratio = Math.min(1, delta / 320);
      currentColor.rgb = [
        currentColor.rgb[0] + (target[0] - currentColor.rgb[0]) * ratio,
        currentColor.rgb[1] + (target[1] - currentColor.rgb[1]) * ratio,
        currentColor.rgb[2] + (target[2] - currentColor.rgb[2]) * ratio,
      ];
      const base = `rgba(${currentColor.rgb.map((c) => Math.round(c * 255)).join(',')},`;
      const accentRgb = renderState.accent ? hexToRgb(renderState.accent) : null;
      const accent = accentRgb ? `rgba(${accentRgb.map((c) => Math.round(c * 255)).join(',')},` : null;

      const cx = width / 2;
      const cy = height / 2;
      const radius = Math.min(width, height) * 0.42 * CLOUD_RADIUS;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      const pitch = Math.sin(time * 0.13) * 0.12;
      const cosP = Math.cos(pitch);
      const sinP = Math.sin(pitch);

      for (let index = 0; index < count; index += 1) {
        let x = positions[index * 3];
        let y = positions[index * 3 + 1];
        let z = positions[index * 3 + 2];

        const wobble = Math.sin(seeds[index] * 41 + time * 1.35) * 0.02 * (0.4 + motion.turbulence) + pulse * 0.03;
        x *= 1 + wobble;
        y *= 1 + wobble;
        z *= 1 + wobble;

        const rx = x * cos + z * sin;
        const rz = -x * sin + z * cos;
        const ry = y * cosP - rz * sinP;
        const rz2 = y * sinP + rz * cosP;

        const persp = 2.6 / (2.6 - rz2 * 0.55);
        const px = cx + rx * radius * persp;
        const py = cy + ry * radius * persp;
        const front = (rz2 + 1) / 2;
        const rim = 1 - Math.abs(rz2);
        const rimGlow = Math.pow(Math.max(0, rim), 1.5);
        const clump =
          0.5 + 0.5 * Math.sin(seeds[index] * 21 + time * 0.55) * Math.sin(seeds[index] * 7.3 - time * 0.23);
        const wisp = Math.min(1, Math.max(0, (clump - 0.15) / 0.75));
        const size = (0.4 + 1.4 * rimGlow) * (radius / 170);
        const alpha = (0.03 + 0.8 * rimGlow) * (0.55 + 0.45 * front) * (0.45 + 0.85 * wisp);

        ctx.beginPath();
        ctx.arc(px, py, Math.max(0.4, size), 0, Math.PI * 2);
        ctx.fillStyle = accent && seeds[index] > 0.72 ? accent + alpha.toFixed(3) + ')' : base + alpha.toFixed(3) + ')';
        ctx.fill();
      }

      ctx.globalCompositeOperation = 'source-over';
    }

    canvasFrame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(canvasFrame);
  }

  const onMotionChange = () => {
    setTier(selectRendererTier({ reducedMotion: reduced.matches, webglAvailable }));
    void tick().then(() => setup());
  };
  const onResize = () => resize();
  reduced.addEventListener('change', onMotionChange);
  window.addEventListener('resize', onResize);
  void tick().then(() => {
    resize();
    setup();
  });

  return () => {
    disposed = true;
    reduced.removeEventListener('change', onMotionChange);
    window.removeEventListener('resize', onResize);
    stopDrawing?.();
  };
}
