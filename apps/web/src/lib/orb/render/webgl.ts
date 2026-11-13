import { buildParticles, hexToRgb } from './particles';
import type { OrbRenderState, Rgb } from './types';

const CLOUD_RADIUS = 0.74;

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

const VERTEX = `#version 300 es
  in vec3 aPos;
  in float aSeed;
  uniform float uTime;
  uniform float uPulse;
  uniform float uTurb;
  uniform float uFlow;
  uniform float uGlow;
  uniform float uPointSize;
  uniform vec3 uColor;
  uniform vec3 uAccent;
  uniform float uHasAccent;
  out vec3 vColor;
  out float vAlpha;

  mat2 rot(float a){ float c = cos(a); float s = sin(a); return mat2(c, -s, s, c); }

  void main(){
    float n = sin(aSeed * 41.0 + uTime * 1.35) * 0.5 + sin(aSeed * 97.0 - uTime * 0.85) * 0.5;
    float breathe = 1.0 + n * (0.018 + uTurb * 0.05) + uPulse * 0.055 * (0.5 + 0.5 * n);

    vec3 p = aPos * breathe * ${CLOUD_RADIUS};

    float yaw = uTime * (0.06 + uFlow * 0.16) + uPulse * 0.08;
    float pitch = sin(uTime * 0.13) * (0.05 + uTurb * 0.12);

    p.xz = rot(yaw) * p.xz;
    p.yz = rot(pitch) * p.yz;

    float focal = 2.6;
    float persp = focal / (focal - p.z * 0.55);
    float front = smoothstep(-1.0, 1.0, p.z);

    gl_Position = vec4(p.x * persp, p.y * persp, 0.0, 1.0);

    // Rim weighting is what makes the shell read as hollow: particles
    // whose normal faces the camera fade, silhouette particles glow.
    float rim = 1.0 - abs(p.z / max(0.001, length(p)));
    float rimGlow = pow(clamp(rim, 0.0, 1.0), 1.8);

    // Coarse angular clumping gives the wispy, uneven rim instead of a
    // uniform dot circle.
    float clump = 0.5 + 0.5 * sin(aSeed * 21.0 + uTime * 0.55) * sin(aSeed * 7.3 - uTime * 0.23);
    float wisp = smoothstep(0.15, 0.9, clump);

    float size = uPointSize * persp * (0.45 + 0.95 * rimGlow) * (0.8 + n * 0.35);
    gl_PointSize = clamp(size, 1.0, 7.0);

    vAlpha = (0.012 + 0.95 * rimGlow) * (0.55 + 0.45 * front) * (0.45 + 0.85 * wisp);
    vColor = uColor;
    if (uHasAccent > 0.5) {
      vColor = mix(uColor, uAccent, step(0.72, aSeed));
    }
  }`;

const FRAGMENT = `#version 300 es
  precision highp float;
  in vec3 vColor;
  in float vAlpha;
  uniform float uOpacity;
  out vec4 outColor;

  void main(){
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c);
    if (d > 0.5) discard;
    float falloff = smoothstep(0.5, 0.08, d);
    outColor = vec4(vColor, falloff * vAlpha * uOpacity);
  }`;

function compile(gl: WebGL2RenderingContext, type: number, source: string) {
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

export function setupWebgl(options: WebglOptions): (() => void) | null {
  const { gl, canvas } = options;
  const vs = compile(gl, gl.VERTEX_SHADER, VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
  const program = gl.createProgram();
  if (!vs || !fs || !program) {
    options.onFailed();
    return null;
  }
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    options.onFailed();
    return null;
  }
  gl.useProgram(program);

  const count = window.matchMedia('(max-width: 767px)').matches ? 2200 : 3400;
  const { positions, seeds } = buildParticles(count);

  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
  const positionLocation = gl.getAttribLocation(program, 'aPos');
  gl.enableVertexAttribArray(positionLocation);
  gl.vertexAttribPointer(positionLocation, 3, gl.FLOAT, false, 0, 0);

  const seedBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, seedBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, seeds, gl.STATIC_DRAW);
  const seedLocation = gl.getAttribLocation(program, 'aSeed');
  gl.enableVertexAttribArray(seedLocation);
  gl.vertexAttribPointer(seedLocation, 1, gl.FLOAT, false, 0, 0);

  const uniforms = {
    time: gl.getUniformLocation(program, 'uTime'),
    pulse: gl.getUniformLocation(program, 'uPulse'),
    turb: gl.getUniformLocation(program, 'uTurb'),
    flow: gl.getUniformLocation(program, 'uFlow'),
    glow: gl.getUniformLocation(program, 'uGlow'),
    pointSize: gl.getUniformLocation(program, 'uPointSize'),
    color: gl.getUniformLocation(program, 'uColor'),
    accent: gl.getUniformLocation(program, 'uAccent'),
    hasAccent: gl.getUniformLocation(program, 'uHasAccent'),
    opacity: gl.getUniformLocation(program, 'uOpacity'),
  };

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  gl.disable(gl.DEPTH_TEST);

  let frame = 0;
  const start = performance.now();
  let last = start;

  const onLost = (event: Event) => {
    event.preventDefault();
    options.onContextLost();
  };
  canvas.addEventListener('webglcontextlost', onLost);

  function draw(now: number) {
    if (options.isDisposed()) return;
    frame = requestAnimationFrame(draw);
    if (document.hidden) return;
    const delta = Math.min(64, now - last);
    last = now;
    const time = (now - start) / 1000;
    const pulse = options.getPulse();

    options.resize();
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
    options.monitorFps(delta);
    const motion = options.renderState.motion;
    const pointScale = (canvas.height / 340) * (1.7 + motion.particleActivity * 1.2);

    gl.uniform1f(uniforms.time, time);
    gl.uniform1f(uniforms.pulse, pulse);
    gl.uniform1f(uniforms.turb, motion.turbulence);
    gl.uniform1f(uniforms.flow, Math.max(0.05, motion.flowRate));
    gl.uniform1f(uniforms.glow, motion.edgeGlow);
    gl.uniform1f(uniforms.pointSize, Math.min(9, pointScale));
    gl.uniform3f(uniforms.color, options.currentColor.rgb[0], options.currentColor.rgb[1], options.currentColor.rgb[2]);
    gl.uniform3f(uniforms.accent, accent[0], accent[1], accent[2]);
    gl.uniform1f(uniforms.hasAccent, options.renderState.accent ? 1 : 0);
    gl.uniform1f(uniforms.opacity, motion.opacity);
    gl.drawArrays(gl.POINTS, 0, count);
  }

  frame = requestAnimationFrame(draw);

  return () => {
    cancelAnimationFrame(frame);
    canvas.removeEventListener('webglcontextlost', onLost);
  };
}
