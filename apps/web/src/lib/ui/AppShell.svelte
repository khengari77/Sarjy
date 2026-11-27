<script lang="ts">
  import { onMount, setContext, tick } from 'svelte';
  import { page } from '$app/state';
  import type { ShellProps } from './shellTypes';
  import ThemeSwitch from './ThemeSwitch.svelte';
  import SignOut from '../auth/SignOut.svelte';
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
  let mainColumn = $state<HTMLElement | null>(null);

  // Floating overlays (the change toast) use the dock's measured height and
  // the main column's centre, so they clear the controls/composer at every
  // viewport and stay over the stage when the rail is docked.
  $effect(() => {
    const dock = controlsRegion;
    const column = mainColumn;
    if (!dock || !column) return;
    const root = document.documentElement;
    const update = () => {
      const dockRect = dock.getBoundingClientRect();
      const columnRect = column.getBoundingClientRect();
      root.style.setProperty('--dock-h', `${window.innerHeight - dockRect.top}px`);
      root.style.setProperty('--main-column-center', `${columnRect.left + columnRect.width / 2}px`);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(dock);
    observer.observe(column);
    window.addEventListener('resize', update);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', update);
      root.style.removeProperty('--dock-h');
      root.style.removeProperty('--main-column-center');
    };
  });

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

    <div class="main-column" bind:this={mainColumn}>
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
          <!-- The protected deployment's session control belongs to the header
               cluster, not to a floating overlay over these buttons. -->
          {#if page.data.protectedMode}
            <SignOut />
          {/if}
          <ThemeSwitch {theme} />
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
