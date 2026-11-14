<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { selectRendererTier, type OrbPresentation, type RendererTier } from './orbState';
  import { ORB_PALETTES } from './palette';
  import { staticGlyph } from './staticGlyph';
  import { startOrbRenderer } from './render/renderer';
  import type { OrbRenderState, OrbTierState } from './render/types';

  interface Props {
    presentation: OrbPresentation;
    micLevel?: number;
    outputLevel?: number;
    label: string;
    statusLabel?: string;
    disabled?: boolean;
    onactivate?: () => void;
  }

  let {
    presentation,
    micLevel = 0,
    outputLevel = 0,
    label,
    statusLabel = '',
    disabled = false,
    onactivate,
  }: Props = $props();

  let canvas: HTMLCanvasElement | undefined = $state();
  let tier: RendererTier = $state('static');
  let failed = $state(false),
    surfaceKey = $state(0);
  let starting = false;
  let surfaceGeneration = 0;
  let stopRenderer: (() => void) | undefined;
  // Survives canvas remounts: the renderer records here that it was demoted,
  // so the replacement surface starts one step down instead of re-probing up.
  const tierState: OrbTierState = { demoted: false };

  // Plain mutable bridge read by the animation loop; updated by the effect below.
  const renderState: OrbRenderState = {
    // First frame before the presentation effect runs; dark matches the
    // tokens.css SSR fallback and still comes from the shared palette.
    color: ORB_PALETTES.dark[0],
    accent: null,
    motion: {
      breath: 0.2,
      flowRate: 0.18,
      turbulence: 0.18,
      edgeGlow: 0.3,
      particleActivity: 0.2,
      radialPulse: 0,
      hueMix: 0,
      opacity: 1,
    },
  };

  const isListening = $derived(presentation.effectivePhase === 'listening');
  const isSpeaking = $derived(presentation.effectivePhase === 'speaking');
  const pulse = $derived(isListening ? micLevel : isSpeaking ? outputLevel : 0);

  onMount(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const probe = document.createElement('canvas');
    tier = selectRendererTier({ reducedMotion: reduced.matches, webglAvailable: Boolean(probe.getContext('webgl2')) });
    begin();
    return () => {
      surfaceGeneration += 1;
      stopRenderer?.();
      stopRenderer = undefined;
    };
  });

  function begin() {
    const token = ++surfaceGeneration;
    void tick().then(() => {
      // A newer remount or unmount wins over this pending start.
      if (token !== surfaceGeneration || !canvas) return;
      stopRenderer?.();
      starting = true;
      stopRenderer = startOrbRenderer(
        canvas,
        renderState,
        {
          getPulse: () => pulse,
          onTier: handleTier,
          onFailed: () => (failed = true),
        },
        tierState,
      );
      starting = false;
    });
  }

  /**
   * A canvas cannot hand out a 2D context once it owns a WebGL2 context, and a
   * tier change that adds/removes the canvas leaves the renderer holding a
   * detached node. Both need a fresh element, so remount the surface and start
   * the next tier on it instead of collapsing to static.
   */
  function handleTier(value: RendererTier) {
    const previous = tier;
    tier = value;
    if (starting) return;
    const needsSurface =
      (previous === 'enhanced' && value === 'light') || (previous === 'static' && value !== 'static');
    if (!needsSurface) return;
    stopRenderer?.();
    stopRenderer = undefined;
    surfaceKey += 1;
    begin();
  }

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
</script>

<button
  class="orb-button"
  type="button"
  aria-label={label}
  {disabled}
  data-renderer={failed ? 'static' : tier}
  data-theme={presentation.theme}
  data-phase={presentation.effectivePhase}
  data-mic-level={micLevel.toFixed(2)}
  data-output-level={outputLevel.toFixed(2)}
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
    {#key surfaceKey}
      <canvas bind:this={canvas} class="orb-canvas" style:opacity={presentation.motionPreset.opacity} aria-hidden="true"
      ></canvas>
    {/key}
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
      radial-gradient(circle at 42% 36%, color-mix(in srgb, var(--orb-accent) 52%, white) 0%, transparent 34%),
      radial-gradient(
        circle at 50% 50%,
        color-mix(in srgb, var(--orb-color) 80%, white) 0%,
        color-mix(in srgb, var(--orb-color) 62%, transparent) 36%,
        color-mix(in srgb, var(--orb-color) 34%, transparent) 62%,
        transparent 80%
      ),
      radial-gradient(
        circle at 50% 50%,
        transparent 60%,
        color-mix(in srgb, var(--orb-color) 24%, transparent) 76%,
        transparent 90%
      );
    box-shadow:
      inset 0 0 40px color-mix(in srgb, var(--orb-accent) 30%, transparent),
      0 0 48px color-mix(in srgb, var(--orb-color) 24%, transparent);
  }

  .orb-static-state {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    color: color-mix(in srgb, var(--orb-color) 55%, var(--text-primary));
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
