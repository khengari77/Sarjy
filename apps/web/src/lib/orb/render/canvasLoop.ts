import type { MotionPreset } from '../orbState';
import { hexToRgb } from './colors';
import { buildParticles } from './particles';
import type { OrbRenderState, Rgb } from './types';

const CLOUD_RADIUS = 0.74;

export interface CanvasLoopOptions {
  canvas: HTMLCanvasElement;
  renderState: OrbRenderState;
  currentColor: { rgb: Rgb };
  currentMotion: MotionPreset;
  getPulse(): number;
  smoothMotion(delta: number): void;
  monitorFps(delta: number): void;
  resize(): void;
  isDisposed(): boolean;
}

export function startCanvasDrawing(options: CanvasLoopOptions): () => void {
  const { canvas, renderState, currentColor, currentMotion } = options;
  const ctx = canvas.getContext('2d');
  if (!ctx) return () => {};
  const context = ctx;
  const count = 420;
  const { positions, seeds } = buildParticles(count);
  let angle = 0;
  let frame = 0;
  let last = performance.now();
  const start = last;

  function draw(now: number) {
    if (options.isDisposed()) return;
    frame = requestAnimationFrame(draw);
    if (document.hidden) return;
    const delta = Math.min(64, now - last);
    last = now;
    const time = (now - start) / 1000;
    const pulse = options.getPulse();
    options.smoothMotion(delta);
    options.monitorFps(delta);
    angle += (delta / 1000) * (0.12 + currentMotion.flowRate * 0.35 + pulse * 0.2);

    options.resize();
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.globalCompositeOperation = 'lighter';

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

      const wobble = Math.sin(seeds[index] * 41 + time * 1.35) * 0.02 * (0.4 + currentMotion.turbulence) + pulse * 0.03;
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
      const clump = 0.5 + 0.5 * Math.sin(seeds[index] * 21 + time * 0.55) * Math.sin(seeds[index] * 7.3 - time * 0.23);
      const wisp = Math.min(1, Math.max(0, (clump - 0.15) / 0.75));
      const size = (0.4 + 1.4 * rimGlow) * (radius / 170);
      const alpha = (0.03 + 0.8 * rimGlow) * (0.55 + 0.45 * front) * (0.45 + 0.85 * wisp);

      context.beginPath();
      context.arc(px, py, Math.max(0.4, size), 0, Math.PI * 2);
      context.fillStyle =
        accent && seeds[index] > 0.72 ? accent + alpha.toFixed(3) + ')' : base + alpha.toFixed(3) + ')';
      context.fill();
    }

    context.globalCompositeOperation = 'source-over';
  }

  frame = requestAnimationFrame(draw);
  return () => cancelAnimationFrame(frame);
}
