import type { MotionPreset } from '../orbState';
import { ORB_CLOUD_HUES, type ThemeName } from '../palette';
import { hexToRgb } from './colors';
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
 *
 * Body layer: one soft filled halo disc, a diffuse whitish centre, pastel hue
 * islands at the mid radii and ~10 sparse dots. Detail layer: a few thin wisp
 * arcs and faint billows. Nothing here strokes an outline around the sphere.
 */
function buildCloudTexture(palette: readonly string[], phase: Rgb, seed: number, wisp: boolean): HTMLCanvasElement {
  const texture = document.createElement('canvas');
  texture.width = TEXTURE_SIZE;
  texture.height = TEXTURE_SIZE;
  const ctx = texture.getContext('2d');
  if (!ctx) return texture;

  const center = TEXTURE_SIZE / 2;
  const colours = palette.map((value) => hexToRgb(value));
  const white: Rgb = [1, 1, 1];
  const light = colours[5] ?? phase;
  const random = mulberry32(seed);
  // The cloud fills almost to the CSS ring (body radius ~0.96 of the half-width).
  const outer = TEXTURE_SIZE * 0.43;

  if (!wisp) {
    // The halo disc is CSS (.orb-glow); the texture is only the cloud.
    // Broad translucent body that dissolves toward the edge.
    const body = ctx.createRadialGradient(center, center, 0, center, center, outer * 1.1);
    body.addColorStop(0, rgba(light, 0.84));
    body.addColorStop(0.45, rgba(colours[0] ?? phase, 0.62));
    body.addColorStop(0.86, rgba(colours[0] ?? phase, 0.34));
    body.addColorStop(1, rgba(colours[0] ?? phase, 0));
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(center, center, outer * 1.1, 0, Math.PI * 2);
    ctx.fill();

    // Hue islands at the mid radii: lavender, gold, mint, coral, cyan.
    const hues = [4, 2, 1, 3, 0];
    for (let index = 0; index < 10; index += 1) {
      const angle = (index / 10) * Math.PI * 2 + random() * 0.6;
      const distance = outer * (0.38 + random() * 0.3);
      const x = center + Math.cos(angle) * distance;
      const y = center + Math.sin(angle) * distance;
      const radius = TEXTURE_SIZE * (0.1 + random() * 0.07);
      const colour = colours[hues[index % hues.length] ?? 0] ?? phase;
      const blob = ctx.createRadialGradient(x, y, 0, x, y, radius);
      blob.addColorStop(0, rgba(colour, 0.5));
      blob.addColorStop(0.6, rgba(colour, 0.2));
      blob.addColorStop(1, rgba(colour, 0));
      ctx.fillStyle = blob;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    // Bright diffuse centre plus the current phase tint.
    const core = ctx.createRadialGradient(center, center, 0, center, center, outer * 0.62);
    core.addColorStop(0, rgba(white, 0.7));
    core.addColorStop(0.5, rgba(light, 0.3));
    core.addColorStop(1, rgba(light, 0));
    ctx.fillStyle = core;
    ctx.fillRect(0, 0, TEXTURE_SIZE, TEXTURE_SIZE);

    // Phase tint stays inside the body so it can never read as a grey rim.
    const tint = ctx.createRadialGradient(center, center, 0, center, center, outer * 0.78);
    tint.addColorStop(0, rgba(phase, 0.1));
    tint.addColorStop(0.55, rgba(phase, 0.07));
    tint.addColorStop(1, rgba(phase, 0));
    ctx.fillStyle = tint;
    ctx.fillRect(0, 0, TEXTURE_SIZE, TEXTURE_SIZE);

    // Ten sparse dots in four loose clusters near the rim, not an even scatter.
    const spots: ReadonlyArray<readonly [number, number, number]> = [
      [-0.34, 0.86, 1.5],
      [-0.27, 0.89, 1.1],
      [-0.4, 0.8, 1],
      [-0.9, 0.3, 1.4],
      [-0.93, 0.22, 1],
      [0.93, 0.05, 1.3],
      [0.95, -0.04, 1],
      [0.91, -0.12, 0.9],
      [0.12, -0.92, 1.3],
      [0.2, -0.9, 1],
    ];
    ctx.fillStyle = rgba(white, 0.78);
    for (const [x, y, size] of spots) {
      ctx.beginPath();
      ctx.arc(center + x * center, center - y * center, size, 0, Math.PI * 2);
      ctx.fill();
    }
    return texture;
  }

  // Detail layer: a few faint billows and four thin wisp arcs.
  for (let index = 0; index < 6; index += 1) {
    const angle = random() * Math.PI * 2;
    const distance = Math.sqrt(random()) * outer * 0.6;
    const x = center + Math.cos(angle) * distance;
    const y = center + Math.sin(angle) * distance;
    const radius = TEXTURE_SIZE * (0.06 + random() * 0.07);
    const colour = colours[Math.floor(random() * colours.length)] ?? phase;
    const blob = ctx.createRadialGradient(x, y, 0, x, y, radius);
    blob.addColorStop(0, rgba(colour, 0.14));
    blob.addColorStop(1, rgba(colour, 0));
    ctx.fillStyle = blob;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.lineWidth = 1;
  ctx.lineCap = 'round';
  ctx.strokeStyle = rgba([0.95 + 0.05 * light[0], 0.95 + 0.05 * light[1], 1], 0.55);
  // Four thin crescents (the shader's wisps): fixed centres, short spans.
  for (let index = 0; index < 4; index += 1) {
    const arcCentreX = center + (-0.3 + 0.26 * index) * center;
    const arcCentreY = center - (0.08 + 0.14 * Math.sin(index * 2.1)) * center;
    const arcRadius = (0.26 + 0.07 * index) * center;
    const heading = -(1.2 + index * 0.5);
    ctx.beginPath();
    ctx.arc(arcCentreX, arcCentreY, arcRadius, heading - 0.7, heading + 0.7);
    ctx.stroke();
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
    const theme = renderState.theme;
    if (theme === cacheTheme && renderState.color === cacheColor && bodyTexture && detailTexture) return;
    cacheTheme = theme;
    cacheColor = renderState.color;
    // Observable proof that the renderer consumed the presentation's theme,
    // not merely that the document carries one.
    canvas.dataset.rendererTheme = theme;
    const palette = ORB_CLOUD_HUES[theme];
    const phase = hexToRgb(renderState.color);
    bodyTexture = buildCloudTexture(palette, phase, 0x5a17, false);
    detailTexture = buildCloudTexture(palette, phase, 0x9e21, true);
  }
  ensureCache();

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
    bodyTexture = null;
    detailTexture = null;
  };
}
