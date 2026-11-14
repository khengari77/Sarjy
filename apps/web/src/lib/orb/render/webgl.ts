import type { ThemeName } from '../palette';
import { hexToRgb } from './colors';
import { FRAGMENT, VERTEX } from './shaders';
import { currentTheme, paletteFor, watchTheme } from './theme';
import type { OrbRenderState, Rgb } from './types';

export interface WebglOptions {
  gl: WebGL2RenderingContext;
  canvas: HTMLCanvasElement;
  renderState: OrbRenderState;
  currentColor: { rgb: Rgb };
  getPulse(): number;
  smoothMotion(delta: number): void;
  monitorFps(delta: number): void;
  resize(): void;
  isDisposed(): boolean;
  onFailed(): void;
  onContextLost(): void;
}

/*
 * A single full-screen triangle evaluates a 2D cloud volume in the fragment
 * shader. There is no raymarcher and no per-frame geometry upload: three
 * palette fields, one billow field and a high-frequency detail field are
 * enough to read as a filled, softly lit sphere.
 */
function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function paletteValues(theme: ThemeName): Float32Array {
  const colours = paletteFor(theme);
  const values = new Float32Array(18);
  for (let index = 0; index < 6; index += 1) {
    const rgb = hexToRgb(colours[index] ?? colours[0]);
    values[index * 3] = rgb[0];
    values[index * 3 + 1] = rgb[1];
    values[index * 3 + 2] = rgb[2];
  }
  return values;
}

function discardShaders(gl: WebGL2RenderingContext, ...shaders: (WebGLShader | null)[]): void {
  for (const shader of shaders) {
    if (shader) gl.deleteShader(shader);
  }
}

function releaseProgram(gl: WebGL2RenderingContext, program: WebGLProgram, ...shaders: (WebGLShader | null)[]): void {
  gl.deleteProgram(program);
  discardShaders(gl, ...shaders);
}

export function setupWebgl(options: WebglOptions): (() => void) | null {
  const { gl, canvas } = options;
  const vs = compile(gl, gl.VERTEX_SHADER, VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
  const program = gl.createProgram();
  if (!vs || !fs || !program) {
    // Release allocations before failing so a retry does not leak shaders.
    discardShaders(gl, vs, fs);
    if (program) gl.deleteProgram(program);
    options.onFailed();
    return null;
  }
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    releaseProgram(gl, program, vs, fs);
    options.onFailed();
    return null;
  }
  gl.useProgram(program);

  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);

  const uniforms = {
    resolution: gl.getUniformLocation(program, 'uResolution'),
    time: gl.getUniformLocation(program, 'uTime'),
    pulse: gl.getUniformLocation(program, 'uPulse'),
    turb: gl.getUniformLocation(program, 'uTurb'),
    flow: gl.getUniformLocation(program, 'uFlow'),
    glow: gl.getUniformLocation(program, 'uGlow'),
    opacity: gl.getUniformLocation(program, 'uOpacity'),
    bodyShade: gl.getUniformLocation(program, 'uBodyShade'),
    saturation: gl.getUniformLocation(program, 'uSaturation'),
    coreMix: gl.getUniformLocation(program, 'uCoreMix'),
    colors: gl.getUniformLocation(program, 'uColors[0]'),
    phase: gl.getUniformLocation(program, 'uPhase'),
    accent: gl.getUniformLocation(program, 'uAccent'),
    hasAccent: gl.getUniformLocation(program, 'uHasAccent'),
  };

  function applyPalette() {
    const theme = currentTheme();
    gl.uniform3fv(uniforms.colors, paletteValues(theme));
    // Sky Prism needs more body shade and saturation to stay legible on
    // #EAF6FF; Cosmic Cloud is already high contrast on #080F29.
    gl.uniform1f(uniforms.bodyShade, theme === 'light' ? 0.95 : 1);
    gl.uniform1f(uniforms.saturation, theme === 'light' ? 1.6 : 1);
    gl.uniform1f(uniforms.coreMix, theme === 'light' ? 0.18 : 0.42);
  }
  applyPalette();
  const stopThemeWatch = watchTheme(applyPalette);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.disable(gl.DEPTH_TEST);

  let frame = 0;
  const start = performance.now();
  let last = start;
  let lastFrame = start;
  let lastDrawn = -Infinity;
  const lowPower = typeof navigator !== 'undefined' && (navigator.hardwareConcurrency ?? 8) <= 4;
  const minFrameGap = lowPower ? 1000 / 30 - 1 : 0;

  const onLost = (event: Event) => {
    event.preventDefault();
    options.onContextLost();
  };
  canvas.addEventListener('webglcontextlost', onLost);

  function draw(now: number) {
    if (options.isDisposed()) return;
    frame = requestAnimationFrame(draw);
    if (document.hidden) {
      lastFrame = now;
      return;
    }
    // Judge the browser's frame delivery, not our own low-power frame cap:
    // a deliberate 30 fps skip must not read as a slow renderer.
    const frameDelta = Math.min(64, now - lastFrame);
    lastFrame = now;
    if (now - lastDrawn < minFrameGap) return;
    lastDrawn = now;
    const delta = Math.min(64, now - last);
    last = now;
    const time = (now - start) / 1000;
    const pulse = Math.min(1, Math.max(0, options.getPulse()));

    options.resize();
    if (canvas.width < 1 || canvas.height < 1) return;
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    const target = hexToRgb(options.renderState.color);
    const ratio = Math.min(1, delta / 320);
    options.currentColor.rgb = [
      options.currentColor.rgb[0] + (target[0] - options.currentColor.rgb[0]) * ratio,
      options.currentColor.rgb[1] + (target[1] - options.currentColor.rgb[1]) * ratio,
      options.currentColor.rgb[2] + (target[2] - options.currentColor.rgb[2]) * ratio,
    ];
    const accent = options.renderState.accent ? hexToRgb(options.renderState.accent) : [0, 0, 0];
    options.smoothMotion(delta);
    options.monitorFps(frameDelta);
    const motion = options.renderState.motion;

    gl.uniform2f(uniforms.resolution, canvas.width, canvas.height);
    gl.uniform1f(uniforms.time, time);
    gl.uniform1f(uniforms.pulse, pulse);
    gl.uniform1f(uniforms.turb, motion.turbulence);
    gl.uniform1f(uniforms.flow, Math.max(0.05, motion.flowRate));
    gl.uniform1f(uniforms.glow, motion.edgeGlow);
    gl.uniform1f(uniforms.opacity, motion.opacity);
    gl.uniform3f(uniforms.phase, options.currentColor.rgb[0], options.currentColor.rgb[1], options.currentColor.rgb[2]);
    gl.uniform3f(uniforms.accent, accent[0], accent[1], accent[2]);
    gl.uniform1f(uniforms.hasAccent, options.renderState.accent ? 1 : 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  frame = requestAnimationFrame(draw);

  return () => {
    cancelAnimationFrame(frame);
    canvas.removeEventListener('webglcontextlost', onLost);
    stopThemeWatch();
    gl.deleteVertexArray(vao);
    releaseProgram(gl, program, vs, fs);
    // Drop the GPU context eagerly; the element is about to be replaced.
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  };
}
