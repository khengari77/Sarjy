<script lang="ts">
  import { onMount, tick } from 'svelte';
  import {
    applyTheme,
    nextThemePreference,
    readStoredTheme,
    resolveTheme,
    storeTheme,
    themeName,
    type ThemePreference,
  } from './theme';
  import type { ShellProps } from './shellTypes';
  import { createDrawerSession } from './drawerSession.svelte';
  import { browserStorage } from '../storage';
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
  }: ShellProps = $props();
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

  // A panel below the dock breakpoint is a modal drawer: the rest of the shell
  // leaves the accessibility tree and the tab order (see `inert` below), and
  // focus lives inside the drawer until it closes. Docked panels are ordinary
  // complementary regions and never trap focus.
  const overlayPanel = $derived(leftOpen && !docked ? 'left' : rightOpen && !docked ? 'right' : null);
  // A stage dialog (e.g. the end-call confirmation the drawer itself triggers)
  // owns the modal layer, so the drawer's inert background must not cover it.
  const backgroundInert = $derived(overlayPanel !== null && !stageDialogOpen);

  // Theme is cosmetic: the preference lives per browser and switching it
  // never touches the voice session, canvas or orb.
  let preference = $state<ThemePreference>('system');
  let systemDark = $state(false);
  const resolvedTheme = $derived(resolveTheme(preference, systemDark));

  function cycleTheme() {
    preference = nextThemePreference(preference);
    storeTheme(browserStorage(), preference);
    applyTheme(document.documentElement, preference, systemDark);
  }

  function triggerFor(panel: 'left' | 'right'): HTMLButtonElement | null {
    return panel === 'left' ? leftButton : rightButton;
  }

  function panelFor(panel: 'left' | 'right'): HTMLElement | null {
    return panel === 'left' ? leftPanel : rightPanel;
  }

  const session = createDrawerSession({
    overlay: () => overlayPanel,
    panel: panelFor,
    trigger: triggerFor,
    controls: () => controlsRegion,
    openState: (panel) => (panel === 'left' ? leftOpen : rightOpen),
    dialogOpen: () => stageDialogOpen,
    closeOverlays: () => {
      leftOpen = false;
      rightOpen = false;
    },
  });

  function opening(panel: 'left' | 'right') {
    if (panel === 'left') {
      leftOpen = true;
      if (!docked) rightOpen = false;
    } else {
      rightOpen = true;
      if (!docked) leftOpen = false;
    }
  }

  function toggle(panel: 'left' | 'right') {
    const isOpen = panel === 'left' ? leftOpen : rightOpen;
    if (isOpen) void closePanel(panel);
    else opening(panel);
  }

  /**
   * Closes one panel and returns focus to the control that opened it. The
   * drawer's background stops being `inert` in the same Svelte flush, so the
   * focus call waits for `tick()`; otherwise the trigger is still inert and
   * `.focus()` is a no-op, dropping focus to <body>.
   */
  async function closePanel(panel: 'left' | 'right') {
    if (panel === 'left') leftOpen = false;
    else rightOpen = false;
    await tick();
    triggerFor(panel)?.focus({ preventScroll: true });
  }

  function onViewportChange(event: MediaQueryListEvent) {
    const active = document.activeElement as HTMLElement | null;
    const focusInLeft = !!active && !!leftPanel?.contains(active);
    const focusInRight = !!active && !!rightPanel?.contains(active);
    const leftWasOpen = leftOpen;
    const rightWasOpen = rightOpen;

    docked = event.matches;

    if (event.matches) {
      // Overlay drawer → docked panel. Keep the panel the user is in open and
      // docked instead of unmounting the element that currently holds focus.
      // With nothing open, fall back to the desktop default (left dock open).
      leftOpen = leftWasOpen || !rightWasOpen;
      rightOpen = rightWasOpen;
      // No focus move needed: the focused element stays rendered and visible.
      return;
    }

    // Docked panel → overlay. Collapsing the docks removes the focused element
    // from the layout, so deliberately restore focus to the panel's trigger.
    leftOpen = false;
    rightOpen = false;
    void tick().then(() => {
      if (focusInLeft) leftButton?.focus({ preventScroll: true });
      else if (focusInRight) rightButton?.focus({ preventScroll: true });
    });
  }

  onMount(() => {
    const wide = window.matchMedia('(min-width: 1200px)');
    const system = window.matchMedia('(prefers-color-scheme: dark)');

    preference = readStoredTheme(browserStorage());
    systemDark = system.matches;
    docked = wide.matches;
    leftOpen = wide.matches;
    applyTheme(document.documentElement, preference, systemDark);
    hydrated = true;

    const onSchemeChange = (event: MediaQueryListEvent) => {
      systemDark = event.matches;
      if (preference === 'system') applyTheme(document.documentElement, preference, systemDark);
    };
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Tab') {
        session.onTab(event);
        return;
      }
      if (event.key !== 'Escape') return;
      const panel = session.escapeTarget();
      if (panel) void closePanel(panel);
    };

    wide.addEventListener('change', onViewportChange);
    system.addEventListener('change', onSchemeChange);
    window.addEventListener('keydown', onKeydown);
    return () => {
      wide.removeEventListener('change', onViewportChange);
      system.removeEventListener('change', onSchemeChange);
      window.removeEventListener('keydown', onKeydown);
    };
  });
</script>

<div class="shell" {dir}>
  <!-- While a modal drawer is open the header and stage are inert: no pointer
       targets, no tab stops and nothing exposed to assistive technology. -->
  <header class="topbar" inert={backgroundInert ? true : undefined}>
    <div class="brand-row">
      {#if left}
        <button
          bind:this={leftButton}
          class="icon-button"
          aria-label={dir === 'rtl' ? 'إظهار المحادثات' : 'Show conversations'}
          aria-expanded={leftOpen}
          aria-controls="shell-left"
          onclick={() => toggle('left')}
        >
          <span aria-hidden="true">☰</span>
        </button>
      {/if}
      <strong class="brand">سَرْجي <span>SARJY</span></strong>
    </div>
    <div class="header-actions">
      <button
        class="ghost-button theme-button"
        data-resolved-theme={resolvedTheme}
        aria-label={`Theme: ${themeName(preference)}. Activate to switch to ${themeName(nextThemePreference(preference))}.`}
        title={`Theme: ${themeName(preference)}`}
        onclick={cycleTheme}
      >
        <span aria-hidden="true" class="theme-glyph"
          >{preference === 'dark' ? '☾' : preference === 'light' ? '☀' : '◐'}</span
        >
        <span class="theme-label" aria-hidden="true">{themeName(preference)}</span>
      </button>
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
        {@render left()}
      </aside>
    {/if}

    <main class="stage" id="voice-stage">
      <div class="stage-content" inert={backgroundInert ? true : undefined}>
        {#if stage}{@render stage()}{/if}
      </div>
      {#if controls}
        <div class="stage-controls" bind:this={controlsRegion}>{@render controls()}</div>
      {/if}
    </main>

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
