<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { selectRendererTier, type OrbPresentation, type RendererTier } from './orbState';

  interface Props {
    presentation: OrbPresentation;
    micLevel?: number;
    outputLevel?: number;
    label: string;
    disabled?: boolean;
    onactivate?: () => void;
  }

  let { presentation, micLevel = 0, outputLevel = 0, label, disabled = false, onactivate }: Props = $props();

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

  onMount(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const probe = document.createElement('canvas');
    const webglAvailable = Boolean(probe.getContext('webgl2'));
    tier = selectRendererTier({ reducedMotion: reduced.matches, webglAvailable });

    let disposed = false;
    let frame = 0;
    let start = performance.now();
    let last = start;
    let current: [number, number, number] = hexToRgb(presentation.primaryColor);

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
        in vec2 aPos;
        void main(){ gl_Position = vec4(aPos, 0.0, 1.0); }`;

      const fragment = `#version 300 es
        precision highp float;
        out vec4 outColor;
        uniform vec2 uRes;
        uniform float uTime;
        uniform vec3 uColor;
        uniform vec3 uAccent;
        uniform float uHasAccent;
        uniform float uPulse;
        uniform float uTurb;
        uniform float uFlow;
        uniform float uGlow;
        uniform float uOpacity;

        float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float noise(vec2 p){
          vec2 i = floor(p); vec2 f = fract(p);
          vec2 u = f * f * (3.0 - 2.0 * f);
          return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
                     mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
        }
        float fbm(vec2 p){
          float v = 0.0; float a = 0.5;
          for (int i = 0; i < 4; i++){ v += a * noise(p); p *= 2.02; a *= 0.5; }
          return v;
        }

        void main(){
          vec2 uv = (gl_FragCoord.xy * 2.0 - uRes) / min(uRes.x, uRes.y);
          float r = length(uv);
          float ang = atan(uv.y, uv.x) / 6.28318;
          float t = uTime * uFlow;

          float warp = fbm(vec2(ang * 3.0 + t * 0.22, r * 2.4 - t * 0.18));
          float n1 = fbm(vec2(ang * 15.0 - t * 0.55, r * 5.0 + t * 0.35));
          float n2 = fbm(vec2(ang * 39.0 + t * 1.15, r * 9.5 - t * 0.5));
          float n3 = fbm(vec2(ang * 78.0 - t * 1.6, r * 15.0 + t * 0.7));

          float ring = 0.585;
          ring += (warp - 0.5) * (0.05 + uTurb * 0.07);
          ring += (n1 - 0.5) * 0.022 * uTurb;
          ring += uPulse * 0.055 * (0.55 + warp);

          float d = abs(r - ring);
          float band = smoothstep(0.052 + 0.018 * uTurb, 0.0, d);
          float grain = smoothstep(0.36, 0.86, n2);
          float fine = smoothstep(0.55, 0.95, n3);
          float filaments = band * (0.3 + 0.55 * grain + 0.35 * fine);
          float sparks = smoothstep(0.87, 1.0, n3) * smoothstep(0.085, 0.0, d) * 1.6;

          float inner = smoothstep(0.24, 0.47, r) * (1.0 - smoothstep(0.78, 0.98, r));
          float glow = exp(-d * 10.0) * (0.3 + 0.35 * grain) * uGlow;

          float alpha = (band * 0.12 + filaments * 0.42 + sparks + glow) * inner;

          vec3 col = uColor;
          col = mix(col, uAccent, uHasAccent * smoothstep(0.5, 0.95, n2) * 0.45);
          col *= 0.78 + 0.55 * grain + 0.35 * uPulse + 0.25 * uGlow;

          outColor = vec4(col, clamp(alpha, 0.0, 1.0) * uOpacity);
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

      const buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      const location = gl.getAttribLocation(program, 'aPos');
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);

      const uniforms = {
        res: gl.getUniformLocation(program, 'uRes'),
        time: gl.getUniformLocation(program, 'uTime'),
        color: gl.getUniformLocation(program, 'uColor'),
        accent: gl.getUniformLocation(program, 'uAccent'),
        hasAccent: gl.getUniformLocation(program, 'uHasAccent'),
        pulse: gl.getUniformLocation(program, 'uPulse'),
        turb: gl.getUniformLocation(program, 'uTurb'),
        flow: gl.getUniformLocation(program, 'uFlow'),
        glow: gl.getUniformLocation(program, 'uGlow'),
        opacity: gl.getUniformLocation(program, 'uOpacity')
      };

      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

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
        const motion = renderState.motion;

        gl.uniform2f(uniforms.res, canvas.width, canvas.height);
        gl.uniform1f(uniforms.time, time);
        gl.uniform3f(uniforms.color, current[0], current[1], current[2]);
        gl.uniform3f(uniforms.accent, accent[0], accent[1], accent[2]);
        gl.uniform1f(uniforms.hasAccent, renderState.accent ? 1 : 0);
        gl.uniform1f(uniforms.pulse, pulse);
        gl.uniform1f(uniforms.turb, motion.turbulence);
        gl.uniform1f(uniforms.flow, Math.max(0.05, motion.flowRate));
        gl.uniform1f(uniforms.glow, motion.edgeGlow);
        gl.uniform1f(uniforms.opacity, motion.opacity);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      }

      frame = requestAnimationFrame(draw);

      return () => {
        cancelAnimationFrame(frame);
        canvas?.removeEventListener('webglcontextlost', onLost);
      };
    }

    function setupCanvas(ctx: CanvasRenderingContext2D): () => void {
      const count = 220;
      const seeds = Array.from({ length: count }, (_, index) => ({
        angle: (index / count) * Math.PI * 2,
        phase: Math.random() * Math.PI * 2,
        speed: 0.25 + Math.random() * 0.55,
        radius: 0.62 + (Math.random() - 0.5) * 0.05
      }));

      function draw(now: number) {
        if (disposed || !canvas) return;
        frame = requestAnimationFrame(draw);
        if (document.hidden) return;
        const delta = Math.min(64, now - last);
        last = now;
        const time = (now - start) / 1000;
        resize();
        const width = canvas.width;
        const height = canvas.height;
        ctx.clearRect(0, 0, width, height);

        const target = hexToRgb(renderState.color);
        const ratio = Math.min(1, delta / 320);
        current = [
          current[0] + (target[0] - current[0]) * ratio,
          current[1] + (target[1] - current[1]) * ratio,
          current[2] + (target[2] - current[2]) * ratio
        ];
        const color = `rgba(${current.map((c) => Math.round(c * 255)).join(',')},`;
        const accent = renderState.accent;
        const motion = renderState.motion;
        const center = { x: width / 2, y: height / 2 };
        const base = Math.min(width, height) * 0.36;

        ctx.save();
        ctx.globalCompositeOperation = 'lighter';

        ctx.beginPath();
        for (let index = 0; index < count; index += 1) {
          const seed = seeds[index];
          const wobble =
            Math.sin(time * seed.speed * 1.6 + seed.phase) * 0.02 * (0.3 + motion.turbulence) +
            Math.sin(time * 0.7 + seed.angle * 5) * 0.008;
          const radius = base * (seed.radius + wobble + pulse * 0.06 * (0.4 + Math.sin(seed.phase + time)));
          const x = center.x + Math.cos(seed.angle + Math.sin(time * 0.15 + seed.phase) * 0.02) * radius;
          const y = center.y + Math.sin(seed.angle + Math.sin(time * 0.15 + seed.phase) * 0.02) * radius;
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = color + (0.18 + motion.edgeGlow * 0.28).toFixed(3) + ')';
        ctx.lineWidth = Math.max(1, base * 0.035);
        ctx.stroke();

        for (const seed of seeds) {
          const wobble =
            Math.sin(time * seed.speed * 1.9 + seed.phase) * 0.02 * (0.3 + motion.turbulence) +
            Math.sin(time * 0.6 + seed.angle * 7) * 0.01;
          const radius = base * (seed.radius + wobble + pulse * 0.09);
          const x = center.x + Math.cos(seed.angle + time * seed.speed * 0.05) * radius;
          const y = center.y + Math.sin(seed.angle + time * seed.speed * 0.05) * radius;
          const size = (0.6 + (seed.phase % 1) * 1.6) * (1 + pulse);
          ctx.beginPath();
          ctx.arc(x, y, size, 0, Math.PI * 2);
          ctx.fillStyle = (accent && seed.phase > 4.2 ? accent : null) ?? color + '0.55)';
          ctx.globalAlpha = 0.35 + motion.edgeGlow * 0.5;
          ctx.fill();
          ctx.globalAlpha = 1;
        }

        const glow = ctx.createRadialGradient(center.x, center.y, base * 0.55, center.x, center.y, base * 1.35);
        glow.addColorStop(0, color + '0)');
        glow.addColorStop(0.6, color + (0.05 + motion.edgeGlow * 0.08).toFixed(3) + ')');
        glow.addColorStop(1, color + '0)');
        ctx.fillStyle = glow;
        ctx.fillRect(0, 0, width, height);

        ctx.restore();
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
      canvas.dataset.motion = JSON.stringify(presentation.motionPreset);
    }
  });
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
