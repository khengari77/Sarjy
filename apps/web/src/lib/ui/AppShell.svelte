<script lang="ts">
  import { onMount, setContext, tick } from 'svelte';
  import { themeName } from './theme';
  import type { ShellProps } from './shellTypes';
  import { createDrawerSession } from './drawerSession.svelte';
  import { createThemeSession } from './themeSession.svelte';
  import { wrapTab } from './drawerFocus';
  import './shell.css';

  let {
    dir = 'ltr',
    languageLabel = 'العربية',
    onLanguage,
    leftLabel = 'Conversations',
    rightLabel = 'Memory and settings',
    left,
    stage,
    controls,
    right,
    stageDialogOpen = false,
    leftOpen = $bindable(false),
    rightOpen = $bindable(false),
    rightOpener = $bindable(null),
    onthemechange,
    onthemeready,
  }: ShellProps = $props();

  // The document ships as English; follow the UI language on the client.
  $effect(() => {
    document.documentElement.lang = dir === 'rtl' ? 'ar' : 'en';
  });

  // SSR renders both panels closed. The saved theme was already stamped on
  // <html> by the inline boot script, so hydration never reads storage.
  // ≥1200px docks the panels side by side; below that they share the overlay
  // layer, so only one can be open at a time.
  let docked = $state(false);
  // Desktop renders the left dock open before hydration so the first paint
  // matches the post-mount state (no layout shift); narrow viewports keep the
  // SSR-closed drawer off-canvas.
  let hydrated = $state(false);
  let leftButton = $state<HTMLButtonElement | null>(null);
  let rightButton = $state<HTMLButtonElement | null>(null);
  let leftPanel = $state<HTMLElement | null>(null);
  let rightPanel = $state<HTMLElement | null>(null);
  let controlsRegion = $state<HTMLElement | null>(null);
  let dialogRegion = $state<HTMLElement | null>(null);

  // The stage dialog (end-call confirmation) is rendered by VoiceStage but
  // belongs to the shell's modal layer: it is portalled into this sibling
  // region so it is never inside the inert stage. Modal focus belongs to it.
  setContext<() => HTMLElement | null>('shell-dialog-region', () => dialogRegion);

  // A panel below the dock breakpoint is a modal drawer: the rest of the shell
  // leaves the accessibility tree and the tab order (see `inert` below), and
  // focus lives inside the drawer until it closes. Docked panels are ordinary
  // complementary regions and never trap focus.
  const overlayPanel = $derived(leftOpen && !docked ? 'left' : rightOpen && !docked ? 'right' : null);
  // A stage dialog (e.g. the end-call confirmation) owns the modal layer: the
  // header, the stage body and the control strip go inert, and only the dialog
  // region stays interactive. The drawer has already handed off (see below).
  const backgroundInert = $derived(overlayPanel !== null || stageDialogOpen);

  // Theme is cosmetic and owned by the shell; the orb subscribes to `resolved`.
  const theme = createThemeSession((value) => onthemechange?.(value));
  // A voice theme command is presentation-only: it enters through this one
  // setter, which owns persistence and OS resolution. Never a server write.
  onthemeready?.({ set: theme.set });

  /**
   * The header shows light/dark as a segmented control. Selecting the mode
   * that is already the explicit preference returns to `system`, which keeps
   * the OS-following option reachable without a third pill.
   */
  function choose(mode: 'light' | 'dark') {
    theme.set(theme.preference === mode ? 'system' : mode);
  }

  const session = createDrawerSession({
    overlay: () => overlayPanel,
    panel: (panel) => (panel === 'left' ? leftPanel : rightPanel),
    trigger: (panel) => (panel === 'left' ? leftButton : (rightOpener ?? rightButton)),
    controls: () => controlsRegion,
    openState: (panel) => (panel === 'left' ? leftOpen : rightOpen),
    setOpen: (panel, open) => {
      if (panel === 'left') leftOpen = open;
      else rightOpen = open;
    },
    dialogOpen: () => stageDialogOpen,
    closeOverlays: () => {
      leftOpen = false;
      rightOpen = false;
    },
  });

  // Invalidates a pending closePanel() focus restore when the panel is
  // reopened inside the same tick (open → close → open must not steal focus).
  let focusRestoreToken = 0;

  function opening(panel: 'left' | 'right') {
    focusRestoreToken += 1;
    if (panel === 'left') leftOpen = true;
    else [rightOpen, rightOpener] = [true, null]; // the header toggle opens it now
    // Below the dock breakpoint only one overlay drawer can be open at a time.
    if (!docked) {
      if (panel === 'left') rightOpen = false;
      else leftOpen = false;
    }
  }

  function toggle(panel: 'left' | 'right') {
    if (panel === 'left' ? leftOpen : rightOpen) void closePanel(panel);
    else opening(panel);
  }

  /**
   * Closes one panel and returns focus to the control that opened it. The
   * drawer's background stops being `inert` in the same Svelte flush, so the
   * focus call waits for `tick()`; otherwise the trigger is still inert and
   * `.focus()` is a no-op, dropping focus to <body>.
   */
  async function closePanel(panel: 'left' | 'right') {
    const token = ++focusRestoreToken;
    if (panel === 'left') leftOpen = false;
    else rightOpen = false;
    await tick();
    if (token !== focusRestoreToken) return;
    (panel === 'left' ? leftButton : (rightOpener ?? rightButton))?.focus({ preventScroll: true });
  }

  function onViewportChange(event: MediaQueryListEvent) {
    docked = event.matches;
    session.onBreakpoint(event.matches);
  }

  onMount(() => {
    const wide = window.matchMedia('(min-width: 1200px)');
    const stopTheme = theme.start();
    docked = wide.matches;
    leftOpen = wide.matches;
    hydrated = true;

    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Tab') {
        // One trap at a time: the stage dialog wins over the drawer.
        if (stageDialogOpen && dialogRegion) wrapTab(event, [dialogRegion]);
        else session.onTab(event);
        return;
      }
      // The dialog owns Escape while it is open, wherever focus currently is;
      // VoiceStage dismisses it on the same key event.
      if (event.key !== 'Escape' || stageDialogOpen) return;
      const panel = session.escapeTarget();
      if (panel) void closePanel(panel);
    };

    wide.addEventListener('change', onViewportChange);
    window.addEventListener('keydown', onKeydown);
    return () => {
      wide.removeEventListener('change', onViewportChange);
      window.removeEventListener('keydown', onKeydown);
      stopTheme();
    };
  });
</script>

<div class="shell" {dir}>
  <div class="body">
    {#if left}
      <aside
        bind:this={leftPanel}
        class="sidebar sidebar-left"
        id="shell-left"
        data-open={leftOpen}
        data-hydrated={hydrated}
        role={overlayPanel === 'left' ? 'dialog' : undefined}
        aria-modal={overlayPanel === 'left' ? 'true' : undefined}
        aria-label={leftLabel}
        inert={!leftOpen ? true : undefined}
      >
        <div class="sidebar-brand">
          <span class="brand-orb" aria-hidden="true"></span>
          <span class="brand">SARJY</span>
        </div>
        {@render left()}
      </aside>
    {/if}

    <div class="main-column">
      <!-- While a modal drawer is open the header and stage are inert: no
           pointer targets, no tab stops and nothing exposed to assistive
           technology. -->
      <header class="topbar" inert={backgroundInert ? true : undefined}>
        <div class="header-left">
          {#if left}
            <button
              bind:this={leftButton}
              class="icon-button menu-button"
              aria-label={dir === 'rtl' ? 'إظهار المحادثات' : 'Show conversations'}
              aria-expanded={leftOpen}
              aria-controls="shell-left"
              onclick={() => toggle('left')}
            >
              <span aria-hidden="true">☰</span>
            </button>
          {/if}
          <span class="context-label">Voice Context Studio</span>
        </div>
        <div class="header-actions">
          <div class="theme-control" role="group" aria-label="Theme">
            <button
              type="button"
              class="theme-button"
              data-active={theme.resolved === 'light'}
              aria-pressed={theme.preference === 'light'}
              aria-label="Theme: Light"
              title={`${themeName('light')} theme${theme.preference === 'light' ? ' — activate to follow your system' : ''}`}
              onclick={() => choose('light')}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none">
                <circle cx="12" cy="12" r="4.1" stroke="currentColor" stroke-width="1.6" />
                <path
                  d="M12 2.6v2.1M12 19.3v2.1M2.6 12h2.1M19.3 12h2.1M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M18.7 5.3l-1.5 1.5M6.8 17.2l-1.5 1.5"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linecap="round"
                />
              </svg>
            </button>
            <button
              type="button"
              class="theme-button"
              data-active={theme.resolved === 'dark'}
              aria-pressed={theme.preference === 'dark'}
              aria-label="Theme: Dark"
              title={`${themeName('dark')} theme${theme.preference === 'dark' ? ' — activate to follow your system' : ''}`}
              onclick={() => choose('dark')}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none">
                <path
                  d="M20.1 14.4A8.3 8.3 0 0 1 9.6 3.9a8.3 8.3 0 1 0 10.5 10.5Z"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linejoin="round"
                />
              </svg>
            </button>
          </div>
          {#if onLanguage}
            <button class="ghost-button" onclick={onLanguage} lang={dir === 'rtl' ? 'en' : 'ar'}>
              {languageLabel}
            </button>
          {/if}
          {#if right}
            <button
              bind:this={rightButton}
              class="icon-button"
              aria-label={dir === 'rtl' ? 'إظهار الذاكرة والإعدادات' : 'Show memory and settings'}
              aria-expanded={rightOpen}
              aria-controls="shell-right"
              onclick={() => toggle('right')}
            >
              <span aria-hidden="true">☷</span>
            </button>
          {/if}
        </div>
      </header>

      <main class="stage" id="voice-stage">
        <div class="stage-content" inert={backgroundInert ? true : undefined}>
          {#if stage}{@render stage()}{/if}
        </div>
        {#if controls}
          <div class="stage-controls" bind:this={controlsRegion} inert={stageDialogOpen ? true : undefined}>
            {@render controls()}
          </div>
        {/if}
        <!-- Portal target for stage dialogs (VoiceStage moves the confirmation
             here). Never inert, so aria-modal="true" is true. -->
        <div class="stage-dialog" bind:this={dialogRegion}></div>
      </main>
    </div>

    {#if right}
      <aside
        bind:this={rightPanel}
        class="sidebar sidebar-right"
        id="shell-right"
        data-open={rightOpen}
        role={overlayPanel === 'right' ? 'dialog' : undefined}
        aria-modal={overlayPanel === 'right' ? 'true' : undefined}
        aria-label={rightLabel}
        inert={!rightOpen ? true : undefined}
      >
        {@render right()}
      </aside>
    {/if}
  </div>

  {#if overlayPanel}
    <button
      class="scrim"
      aria-label="Close panels"
      aria-hidden="true"
      tabindex="-1"
      onclick={() => void closePanel(overlayPanel === 'left' ? 'left' : 'right')}
    ></button>
  {/if}
</div>
