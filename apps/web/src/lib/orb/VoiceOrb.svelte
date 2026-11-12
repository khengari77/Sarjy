<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { nextRendererTier, selectRendererTier, type OrbPresentation, type RendererTier } from './orbState';

  interface Props {
    presentation: OrbPresentation;
    micLevel?: number;
    outputLevel?: number;
    label: string;
    statusLabel?: string;
    disabled?: boolean;
    onactivate?: () => void;
  }

  let { presentation, micLevel = 0, outputLevel = 0, label, statusLabel = '', disabled = false, onactivate }: Props = $props();

  /** Single tunable size for the particle shell; everything else derives from it. */
  const CLOUD_RADIUS = 0.74;

  let canvas: HTMLCanvasElement | undefined = $state();
  let tier: RendererTier = $state('static');
  let failed = $state(false);

  // Plain mutable bridge read by the animation loop; updated by the effect below.
  const renderState = {
    color: '#4cc9b6',
    accent: null as string | null,
    motion: { breath: 0.2, flowRate: 0.18, turbulence: 0.18, edgeGlow: 0.3, particleActivity: 0.2, radialPulse: 0, hueMix: 0, opacity: 1 }
  };

  const isListening = $derived(presentation.effectivePhase === 'listening');
  const isSpeaking = $derived(presentation.effectivePhase === 'speaking');
  const pulse = $derived(isListening ? micLevel : isSpeaking ? outputLevel : 0);

  function hexToRgb(hex: string): [number, number, number] {
    const value = hex.replace('#', '');
    const parsed = Number.parseInt(value.length === 3 ? value.replace(/(.)/g, '$1$1') : value, 16);
    return [((parsed >> 16) & 255) / 255, ((parsed >> 8) & 255) / 255, (parsed & 255) / 255];
  }

  /** Fibonacci sphere with a little radial jitter so the shell reads as organic. */
  function buildParticles(count: number): { positions: Float32Array; seeds: Float32Array } {
    const positions = new Float32Array(count * 3);
    const seeds = new Float32Array(count);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let index = 0; index < count; index += 1) {
      const y = 1 - (2 * (index + 0.5)) / count;
      const radius = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * index;
      const shell = 0.9 + Math.random() * 0.16;
      positions[index * 3] = Math.cos(theta) * radius * shell;
      positions[index * 3 + 1] = y * shell;
      positions[index * 3 + 2] = Math.sin(theta) * radius * shell;
      seeds[index] = Math.random();
    }
    return { positions, seeds };
  }

  onMount(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const probe = document.createElement('canvas');
    const webglAvailable = Boolean(probe.getContext('webgl2'));
    tier = selectRendererTier({ reducedMotion: reduced.matches, webglAvailable });

    let disposed = false;
    let frame = 0;
    let start = performance.now();
    let last = start;
    let current: [number, number, number] = hexToRgb(renderState.color);
    const currentMotion = { ...renderState.motion };
    let fpsSamples = 0;
    let fpsElapsed = 0;

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
      tier = next;
      if (next === 'static') failed = true;
      void tick().then(() => setup());
    }

    function smoothMotion(delta: number) {
      const ratio = Math.min(1, delta / 320);
      for (const key of ['turbulence', 'flowRate', 'edgeGlow', 'particleActivity', 'opacity'] as const) {
        currentMotion[key] += (renderState.motion[key] - currentMotion[key]) * ratio;
      }
    }

    const onMotionChange = () => {
      tier = selectRendererTier({ reducedMotion: reduced.matches, webglAvailable });
      void tick().then(() => setup());
    };
    reduced.addEventListener('change', onMotionChange);

    let stopDrawing: (() => void) | null = null;

    function setup() {
      stopDrawing?.();
      stopDrawing = null;
      if (!canvas || disposed) return;
      if (tier === 'static') return;

      const gl = tier === 'enhanced' ? canvas.getContext('webgl2', { alpha: true, antialias: true }) : null;
      if (gl) {
        const stop = setupWebgl(gl);
        if (stop) {
          stopDrawing = stop;
          return;
        }
        tier = 'light';
      }
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        tier = 'static';
        return;
      }
      stopDrawing = setupCanvas(ctx);
    }

    function resize() {
      if (!canvas) return;
      const ratio = Math.min(window.devicePixelRatio || 1, isSmallViewport() ? 1.5 : 2);
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(rect.width * ratio));
      canvas.height = Math.max(1, Math.round(rect.height * ratio));
    }

    function isSmallViewport() {
      return window.matchMedia('(max-width: 767px)').matches;
    }

    function setupWebgl(gl: WebGL2RenderingContext): (() => void) | null {
      const vertex = `#version 300 es
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

      const fragment = `#version 300 es
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

      function compile(type: number, source: string) {
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

      const vs = compile(gl.VERTEX_SHADER, vertex);
      const fs = compile(gl.FRAGMENT_SHADER, fragment);
      const program = gl.createProgram();
      if (!vs || !fs || !program) {
        failed = true;
        return null;
      }
      gl.attachShader(program, vs);
      gl.attachShader(program, fs);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        failed = true;
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
        opacity: gl.getUniformLocation(program, 'uOpacity')
      };

      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);
      gl.disable(gl.DEPTH_TEST);

      const onLost = (event: Event) => {
        event.preventDefault();
        failed = true;
        tier = 'static';
        stopDrawing?.();
      };
      canvas?.addEventListener('webglcontextlost', onLost);

      function draw(now: number) {
        if (disposed || !canvas || !gl) return;
        frame = requestAnimationFrame(draw);
        if (document.hidden) return;
        const delta = Math.min(64, now - last);
        last = now;
        const time = (now - start) / 1000;

        resize();
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);

        const target = hexToRgb(renderState.color);
        const ratio = Math.min(1, delta / 320);
        current = [
          current[0] + (target[0] - current[0]) * ratio,
          current[1] + (target[1] - current[1]) * ratio,
          current[2] + (target[2] - current[2]) * ratio
        ];
        const accent = renderState.accent ? hexToRgb(renderState.accent) : [0, 0, 0];
        smoothMotion(delta);
        monitorFps(delta);
        const motion = currentMotion;
        const pointScale = (canvas.height / 340) * (1.7 + motion.particleActivity * 1.2);

        gl.uniform1f(uniforms.time, time);
        gl.uniform1f(uniforms.pulse, pulse);
        gl.uniform1f(uniforms.turb, motion.turbulence);
        gl.uniform1f(uniforms.flow, Math.max(0.05, motion.flowRate));
        gl.uniform1f(uniforms.glow, motion.edgeGlow);
        gl.uniform1f(uniforms.pointSize, Math.min(9, pointScale));
        gl.uniform3f(uniforms.color, current[0], current[1], current[2]);
        gl.uniform3f(uniforms.accent, accent[0], accent[1], accent[2]);
        gl.uniform1f(uniforms.hasAccent, renderState.accent ? 1 : 0);
        gl.uniform1f(uniforms.opacity, motion.opacity);
        gl.drawArrays(gl.POINTS, 0, count);
      }

      frame = requestAnimationFrame(draw);

      return () => {
        cancelAnimationFrame(frame);
        canvas?.removeEventListener('webglcontextlost', onLost);
      };
    }

    function setupCanvas(ctx: CanvasRenderingContext2D): () => void {
      const count = 420;
      const { positions, seeds } = buildParticles(count);
      let angle = 0;

      function draw(now: number) {
        if (disposed || !canvas) return;
        frame = requestAnimationFrame(draw);
        if (document.hidden) return;
        const delta = Math.min(64, now - last);
        last = now;
        const time = (now - start) / 1000;
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
        current = [
          current[0] + (target[0] - current[0]) * ratio,
          current[1] + (target[1] - current[1]) * ratio,
          current[2] + (target[2] - current[2]) * ratio
        ];
        const base = `rgba(${current.map((c) => Math.round(c * 255)).join(',')},`;
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

          const wobble =
            Math.sin(seeds[index] * 41 + time * 1.35) * 0.02 * (0.4 + motion.turbulence) +
            pulse * 0.03;
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

          ctx.beginPath();
          ctx.arc(px, py, Math.max(0.4, size), 0, Math.PI * 2);
          ctx.fillStyle = accent && seeds[index] > 0.72 ? accent + alpha.toFixed(3) + ')' : base + alpha.toFixed(3) + ')';
          ctx.fill();
        }

        ctx.globalCompositeOperation = 'source-over';
      }

      frame = requestAnimationFrame(draw);
      return () => cancelAnimationFrame(frame);
    }

    const onResize = () => resize();
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
      cancelAnimationFrame(frame);
    };
  });

  $effect(() => {
    // Keep the renderer in sync with the derived presentation without
    // re-registering handlers or recreating the canvas.
    renderState.color = presentation.primaryColor;
    renderState.accent = presentation.accent;
    renderState.motion = presentation.motionPreset;
    if (canvas) {
      canvas.dataset.phase = presentation.effectivePhase;
      canvas.dataset.color = presentation.primaryColor;
      canvas.dataset.accent = presentation.accent ?? '';
    }
  });

  function staticGlyph(phase: string): string {
    switch (phase) {
      case 'connecting':
        return '◌';
      case 'listening':
        return '◉';
      case 'thinking':
        return '✦';
      case 'speaking':
        return '◍';
      case 'disconnected':
        return '⊘';
      case 'error':
        return '!';
      default:
        return '●';
    }
  }
</script>

<button
  class="orb-button"
  type="button"
  aria-label={label}
  {disabled}
  data-renderer={failed ? 'static' : tier}
  data-phase={presentation.effectivePhase}
  onclick={() => onactivate?.()}
>
  {#if tier === 'static' || failed}
    <span
      class="orb-static"
      style:--orb-color={presentation.primaryColor}
      style:--orb-accent={presentation.accent ?? presentation.primaryColor}
      aria-hidden="true"
    ></span>
    <span class="orb-static-state" aria-hidden="true">
      <span class="orb-glyph">{staticGlyph(presentation.effectivePhase)}</span>
      {#if statusLabel}<span class="orb-state-text">{statusLabel}</span>{/if}
    </span>
  {:else}
    <canvas
      bind:this={canvas}
      class="orb-canvas"
      style:opacity={presentation.motionPreset.opacity}
      aria-hidden="true"
    ></canvas>
  {/if}
  {#if presentation.showMicSlash}
    <span class="mic-slash" aria-hidden="true">🎙̸</span>
  {/if}
</button>

<style>
  .orb-button {
    position: relative;
    display: grid;
    place-items: center;
    width: clamp(190px, 27vw, 340px);
    aspect-ratio: 1;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    touch-action: manipulation;
  }

  .orb-button:disabled {
    cursor: not-allowed;
    opacity: 0.7;
  }

  .orb-button:hover .orb-canvas,
  .orb-button:focus-visible .orb-canvas {
    filter: brightness(1.12);
  }

  .orb-button:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 6px;
  }

  .orb-canvas {
    width: 100%;
    height: 100%;
    display: block;
    transition: opacity var(--transition-state);
  }

  .orb-static {
    width: 82%;
    aspect-ratio: 1;
    border-radius: 50%;
    background:
      radial-gradient(circle at 50% 50%, transparent 54%, color-mix(in srgb, var(--orb-color) 26%, transparent) 60%, transparent 68%),
      radial-gradient(circle at 50% 50%, transparent 64%, color-mix(in srgb, var(--orb-accent) 12%, transparent) 74%, transparent 82%);
    border: 2px solid color-mix(in srgb, var(--orb-color) 40%, transparent);
    box-shadow: 0 0 48px color-mix(in srgb, var(--orb-color) 22%, transparent);
  }

  .orb-static-state {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    color: color-mix(in srgb, var(--orb-color) 75%, white);
    pointer-events: none;
    text-align: center;
    padding: 18%;
  }

  .orb-glyph {
    font-size: clamp(22px, 5vw, 30px);
    line-height: 1;
    opacity: 0.9;
  }

  .orb-state-text {
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    line-height: 1.3;
  }

  .mic-slash {
    position: absolute;
    inset: auto 50% 18% auto;
    transform: translateX(50%);
    font-size: 22px;
    color: var(--text-muted);
    background: color-mix(in srgb, var(--bg-app) 70%, transparent);
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    padding: 4px 8px;
  }

  @media (max-width: 767px) {
    .orb-button {
      width: clamp(190px, 62vw, 260px);
    }
  }
</style>
