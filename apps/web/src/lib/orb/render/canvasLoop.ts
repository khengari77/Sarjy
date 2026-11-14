import type { MotionPreset } from '../orbState';
import type { ThemeName } from '../palette';
import { hexToRgb } from './colors';
import { currentTheme, paletteFor, watchTheme } from './theme';
import type { OrbRenderState, Rgb } from './types';

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

const TEXTURE_SIZE = 256;

/** Deterministic PRNG so the fallback texture is stable between frames. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function rgba(rgb: Rgb, alpha: number): string {
  const red = Math.round(rgb[0] * 255);
  const green = Math.round(rgb[1] * 255);
  const blue = Math.round(rgb[2] * 255);
  return `rgba(${red},${green},${blue},${alpha.toFixed(3)})`;
}

/**
 * Draw a layered, circular cloud once into an offscreen canvas. The per-frame
 * loop only transforms and composites these cached layers.
 */
function buildCloudTexture(palette: readonly string[], phase: Rgb, seed: number, wisp: boolean): HTMLCanvasElement {
  const texture = document.createElement('canvas');
  texture.width = TEXTURE_SIZE;
  texture.height = TEXTURE_SIZE;
  const ctx = texture.getContext('2d');
  if (!ctx) return texture;

  const center = TEXTURE_SIZE / 2;
  const colours = palette.map((value) => hexToRgb(value));
  const random = mulberry32(seed);
  const outer = TEXTURE_SIZE * 0.44;

  if (!wisp) {
    // Delicate halo, then a broad filled body so the centre never reads hollow.
    const halo = ctx.createRadialGradient(center, center, outer * 0.5, center, center, TEXTURE_SIZE * 0.5);
    halo.addColorStop(0, rgba(colours[0] ?? phase, 0.1));
    halo.addColorStop(0.7, rgba(colours[1] ?? phase, 0.05));
    halo.addColorStop(1, rgba(colours[1] ?? phase, 0));
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, TEXTURE_SIZE, TEXTURE_SIZE);

    const body = ctx.createRadialGradient(center, center, 0, center, center, outer);
    body.addColorStop(0, rgba(colours[5] ?? phase, 0.6));
    body.addColorStop(0.28, rgba(colours[0] ?? phase, 0.68));
    body.addColorStop(0.64, rgba(colours[1] ?? phase, 0.54));
    body.addColorStop(0.86, rgba(colours[2] ?? phase, 0.28));
    body.addColorStop(1, rgba(colours[3] ?? phase, 0));
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(center, center, outer, 0, Math.PI * 2);
    ctx.fill();
  }

  const blobs = wisp ? 20 : 34;
  for (let index = 0; index < blobs; index += 1) {
    const angle = random() * Math.PI * 2;
    const distance = Math.sqrt(random()) * outer * (wisp ? 0.78 : 0.66);
    const x = center + Math.cos(angle) * distance;
    const y = center + Math.sin(angle) * distance;
    const radius = TEXTURE_SIZE * (wisp ? 0.045 + random() * 0.09 : 0.07 + random() * 0.15);
    const colour = colours[Math.floor(random() * colours.length)] ?? phase;
    const blob = ctx.createRadialGradient(x, y, 0, x, y, radius);
    blob.addColorStop(0, rgba(colour, wisp ? 0.15 : 0.22));
    blob.addColorStop(0.55, rgba(colour, wisp ? 0.07 : 0.11));
    blob.addColorStop(1, rgba(colour, 0));
    ctx.fillStyle = blob;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  if (!wisp) {
    // Luminous inner centre plus the current phase tint.
    const core = ctx.createRadialGradient(center, center, 0, center, center, outer * 0.72);
    core.addColorStop(0, rgba(colours[5] ?? phase, 0.52));
    core.addColorStop(0.55, rgba(colours[5] ?? phase, 0.18));
    core.addColorStop(1, rgba(colours[5] ?? phase, 0));
    ctx.fillStyle = core;
    ctx.fillRect(0, 0, TEXTURE_SIZE, TEXTURE_SIZE);

    const tint = ctx.createRadialGradient(center, center, 0, center, center, outer);
    tint.addColorStop(0, rgba(phase, 0.16));
    tint.addColorStop(0.7, rgba(phase, 0.08));
    tint.addColorStop(1, rgba(phase, 0));
    ctx.fillStyle = tint;
    ctx.fillRect(0, 0, TEXTURE_SIZE, TEXTURE_SIZE);
  }

  // Sparse pinpricks, only a handful.
  ctx.fillStyle = rgba(colours[5] ?? phase, 0.5);
  for (let index = 0; index < 10; index += 1) {
    const angle = random() * Math.PI * 2;
    const distance = Math.sqrt(random()) * outer * 0.82;
    const x = center + Math.cos(angle) * distance;
    const y = center + Math.sin(angle) * distance;
    const radius = 0.7 + random() * 1.4;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  return texture;
}

export function startCanvasDrawing(options: CanvasLoopOptions): () => void {
  const { canvas, renderState, currentColor, currentMotion } = options;
  const ctx = canvas.getContext('2d');
  if (!ctx) return () => {};
  const context = ctx;

  let cacheTheme: ThemeName | null = null;
  let cacheColor = '';
  let bodyTexture: HTMLCanvasElement | null = null;
  let detailTexture: HTMLCanvasElement | null = null;

  function ensureCache() {
    const theme = currentTheme();
    if (theme === cacheTheme && renderState.color === cacheColor && bodyTexture && detailTexture) return;
    cacheTheme = theme;
    cacheColor = renderState.color;
    const palette = paletteFor(theme);
    const phase = hexToRgb(renderState.color);
    bodyTexture = buildCloudTexture(palette, phase, 0x5a17, false);
    detailTexture = buildCloudTexture(palette, phase, 0x9e21, true);
  }
  ensureCache();
  const stopThemeWatch = watchTheme(() => {
    cacheTheme = null;
  });

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
    const pulse = Math.min(1, Math.max(0, options.getPulse()));
    options.smoothMotion(delta);
    options.monitorFps(delta);
    angle += (delta / 1000) * (0.05 + currentMotion.flowRate * 0.3 + pulse * 0.1);

    options.resize();
    const width = canvas.width;
    const height = canvas.height;
    if (width < 1 || height < 1) return;

    const target = hexToRgb(renderState.color);
    const ratio = Math.min(1, delta / 320);
    currentColor.rgb = [
      currentColor.rgb[0] + (target[0] - currentColor.rgb[0]) * ratio,
      currentColor.rgb[1] + (target[1] - currentColor.rgb[1]) * ratio,
      currentColor.rgb[2] + (target[2] - currentColor.rgb[2]) * ratio,
    ];
    ensureCache();

    context.clearRect(0, 0, width, height);
    context.globalCompositeOperation = 'source-over';
    context.save();
    context.translate(width / 2, height / 2);

    const size = Math.min(width, height);
    const breathe = 1 + Math.sin(time * 0.52) * 0.012 + pulse * 0.028 + currentMotion.radialPulse * 0.01;
    context.globalAlpha = Math.min(1, currentMotion.opacity);
    context.rotate(angle * 0.22);
    context.scale(breathe, breathe);
    if (bodyTexture) context.drawImage(bodyTexture, -size / 2, -size / 2, size, size);

    // Second cached layer drifts the other way, so the interior billows move
    // without the silhouette spinning like a rigid ball.
    context.rotate(-angle * 0.62 + Math.sin(time * 0.31) * 0.08);
    context.globalAlpha = Math.min(1, currentMotion.opacity * (0.55 + currentMotion.edgeGlow * 0.35));
    if (detailTexture) context.drawImage(detailTexture, -size / 2, -size / 2, size, size);

    context.restore();
    context.globalAlpha = 1;
  }

  frame = requestAnimationFrame(draw);
  return () => {
    cancelAnimationFrame(frame);
    stopThemeWatch();
    bodyTexture = null;
    detailTexture = null;
  };
}
