<script lang="ts">
  import { onMount } from 'svelte';
  import { selectRendererTier, type RendererTier } from '../orb/rendererTier';
  import { authorizeDeveloper, DevReadError, fetchDevContext, fetchDevOperations, type DevFetcher } from './contextApi';
  import type { DiagnosticsModel } from './panelModel';
  import { buildDiagnostics } from './panelModel';
  import DiagnosticsSections from './DiagnosticsSections.svelte';
  import { createModeController, initialDevModeState } from './mode';

  let { conversationId, arabic = false }: { conversationId: string | null; arabic?: boolean } = $props();

  let modeState = $state(initialDevModeState());
  let diagnostics = $state<DiagnosticsModel | null>(null);
  let loading = $state(false);
  let readError = $state('');
  let rendererTier = $state<RendererTier>('light');
  let loadedKey = '';
  let loadGeneration = 0;
  let authAttempted: string | null = null;

  const fetcher: DevFetcher = (input, init) => fetch(input, init);
  const controller = createModeController(modeState, {
    getStorage: () => (typeof localStorage === 'undefined' ? null : localStorage),
    authorize: () => authorizeDeveloper(fetcher, conversationId),
  });

  function detectRendererTier(): RendererTier {
    if (typeof window === 'undefined') return 'light';
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    let webglAvailable = false;
    try {
      webglAvailable = document.createElement('canvas').getContext('webgl') !== null;
    } catch {
      webglAvailable = false;
    }
    return selectRendererTier({ reducedMotion, webglAvailable });
  }

  async function loadDiagnostics(id: string) {
    const generation = ++loadGeneration;
    loading = true;
    readError = '';
    try {
      const [context, operations] = await Promise.all([fetchDevContext(fetcher, id), fetchDevOperations(fetcher, id)]);
      if (generation !== loadGeneration) return;
      diagnostics = buildDiagnostics({ context, operations: operations.operations, rendererTier });
    } catch (error) {
      if (generation !== loadGeneration) return;
      diagnostics = null;
      readError = error instanceof DevReadError ? error.code : 'DEV_CONTEXT_UNAVAILABLE';
      if (error instanceof DevReadError && error.code === 'DEV_CONTEXT_UNAUTHORIZED') {
        controller.requireSimple(readError);
      }
    } finally {
      if (generation === loadGeneration) loading = false;
    }
  }

  async function toggleMode() {
    loadedKey = '';
    await controller.toggle();
  }

  onMount(() => {
    rendererTier = detectRendererTier();
  });

  // The conversation id arrives after boot, so verify access when it does.
  $effect(() => {
    const id = conversationId;
    if (authAttempted === id) return;
    authAttempted = id;
    void controller.hydrate();
  });

  // Developer diagnostics load only while Developer mode is active and a real
  // conversation id exists. This is a read; it never starts a voice session.
  $effect(() => {
    const id = conversationId;
    const active = modeState.mode === 'developer';
    if (!active || !id) {
      diagnostics = null;
      readError = '';
      loadedKey = '';
      return;
    }
    if (loadedKey === id) return;
    loadedKey = id;
    void loadDiagnostics(id);
  });
</script>

<section class="dev-panel" dir={arabic ? 'rtl' : 'ltr'} aria-labelledby="dev-panel-title">
  <header class="panel-head">
    <h2 id="dev-panel-title">{arabic ? 'السياق' : 'Context'}</h2>
    {#if modeState.authorization === 'authorized'}
      <button
        type="button"
        role="switch"
        class="mode-toggle"
        aria-checked={modeState.mode === 'developer'}
        aria-label={arabic ? 'وضع المطوّر' : 'Developer mode'}
        onclick={toggleMode}
      >
        <span class="track" aria-hidden="true"><span class="thumb"></span></span>
        <span>{modeState.mode === 'developer' ? 'Developer' : 'Simple'}</span>
      </button>
    {/if}
  </header>

  {#if modeState.authorization === 'checking'}
    <p class="muted" role="status">{arabic ? 'جارٍ التحقق من صلاحية التشخيص…' : 'Checking diagnostic access…'}</p>
  {:else if modeState.authorization === 'unauthorized'}
    <p class="muted">
      {arabic ? 'تشخيص السياق غير متاح لهذه الجلسة.' : 'Context diagnostics are not available for this session.'}
    </p>
  {:else if modeState.authorization === 'unavailable'}
    <p class="error" role="status">
      {arabic ? 'تعذّر التحقق من صلاحية التشخيص.' : 'Diagnostic access could not be verified.'}
      <button
        type="button"
        class="retry"
        onclick={() => {
          authAttempted = null;
          void controller.hydrate();
        }}
      >
        {arabic ? 'إعادة المحاولة' : 'Retry'}
      </button>
    </p>
  {:else if modeState.mode === 'simple'}
    <p class="muted">
      {arabic
        ? 'الوضع البسيط يخفي تشخيص السياق. بدّل إلى وضع المطوّر لعرض المراجعة والعمليات والفروق.'
        : 'Simple mode hides context diagnostics. Switch to Developer to inspect revision, operations and diffs.'}
    </p>
  {:else}
    {#if loading}
      <p class="muted" role="status">{arabic ? 'جارٍ تحميل التشخيص…' : 'Loading diagnostics…'}</p>
    {:else if readError}
      <p class="error" role="status" data-code={readError}>
        {arabic ? 'تعذّر تحميل التشخيص.' : 'Diagnostics could not be loaded.'}
        <button type="button" class="retry" onclick={() => conversationId && void loadDiagnostics(conversationId)}
          >Retry</button
        >
      </p>
    {:else if !conversationId}
      <p class="muted">{arabic ? 'لا توجد محادثة نشطة بعد.' : 'No conversation is available yet.'}</p>
    {:else if diagnostics}
      <DiagnosticsSections model={diagnostics} />
    {/if}
  {/if}

  {#if modeState.error && modeState.authorization !== 'unauthorized'}
    <p class="error" data-code={modeState.error}>{modeState.error}</p>
  {/if}
</section>

<style>
  .dev-panel {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px 16px;
    border-top: 1px solid var(--border-subtle);
  }

  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }

  h2 {
    margin: 0;
    font-size: 14px;
    letter-spacing: 0.02em;
  }

  .mode-toggle {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 32px;
    padding: 4px 8px;
    font: inherit;
    font-size: 11px;
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    background: transparent;
    color: var(--text-primary);
    cursor: pointer;
  }

  .mode-toggle:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .track {
    position: relative;
    width: 26px;
    height: 14px;
    border-radius: 999px;
    background: var(--surface-hover);
    border: 1px solid var(--border-subtle);
  }

  .thumb {
    position: absolute;
    top: 1px;
    left: 1px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--text-muted);
    transition:
      transform var(--transition-quick),
      background var(--transition-quick);
  }

  .mode-toggle[aria-checked='true'] .thumb {
    transform: translateX(12px);
    background: var(--accent);
  }

  .muted {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--text-muted);
  }

  .error {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--status-error);
  }

  .retry {
    font: inherit;
    font-size: 11px;
    margin-inline-start: 6px;
    padding: 2px 8px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    background: transparent;
    color: var(--text-primary);
    cursor: pointer;
  }
</style>
