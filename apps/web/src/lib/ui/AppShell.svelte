<script lang="ts">
  import type { Snippet } from 'svelte';
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
  import './shell.css';

  interface Props {
    dir?: 'ltr' | 'rtl';
    languageLabel?: string;
    onLanguage?: () => void;
    leftLabel?: string;
    rightLabel?: string;
    left?: Snippet;
    stage?: Snippet;
    right?: Snippet;
  }

  let {
    dir = 'ltr',
    languageLabel = 'العربية',
    onLanguage,
    leftLabel = 'Conversations',
    rightLabel = 'Memory and settings',
    left,
    stage,
    right,
  }: Props = $props();

  // The document ships as English; follow the UI language on the client.
  $effect(() => {
    document.documentElement.lang = dir === 'rtl' ? 'ar' : 'en';
  });

  // SSR renders both panels closed. The saved theme was already stamped on
  // <html> by the inline boot script, so hydration never reads storage.
  let leftOpen = $state(false);
  let rightOpen = $state(false);
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
  let openOrigin = $state<'left' | 'right' | null>(null);

  // A panel below the dock breakpoint is a modal drawer: the rest of the shell
  // leaves the accessibility tree and the tab order (see `inert` below), and
  // focus lives inside the drawer until it closes. Docked panels are ordinary
  // complementary regions and never trap focus.
  const overlayPanel = $derived(leftOpen && !docked ? 'left' : rightOpen && !docked ? 'right' : null);
  const backgroundInert = $derived(overlayPanel !== null);

  // Theme is cosmetic: the preference lives per browser and switching it
  // never touches the voice session, canvas or orb.
  let preference = $state<ThemePreference>('system');
  let systemDark = $state(false);
  const resolvedTheme = $derived(resolveTheme(preference, systemDark));

  function syncTheme() {
    applyTheme(document.documentElement, preference, systemDark);
  }

  function cycleTheme() {
    preference = nextThemePreference(preference);
    storeTheme(window.localStorage, preference);
    syncTheme();
  }

  const FOCUSABLE = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  /** First element a keyboard user can actually reach inside `root`. */
  function firstFocusable(root: HTMLElement | null): HTMLElement | null {
    if (!root) return null;
    const candidates = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
    return (
      candidates.find(
        (element) => element.tabIndex >= 0 && element.getClientRects().length > 0 && !element.closest('[inert]'),
      ) ?? null
    );
  }

  function triggerFor(panel: 'left' | 'right'): HTMLButtonElement | null {
    return panel === 'left' ? leftButton : rightButton;
  }

  function panelFor(panel: 'left' | 'right'): HTMLElement | null {
    return panel === 'left' ? leftPanel : rightPanel;
  }

  // Opening an overlay drawer moves focus to its first meaningful control.
  // The drawer content is always rendered, so this runs after Svelte has made
  // it visible. Docked panels are not dialogs and never steal focus.
  let focusedOverlay: 'left' | 'right' | null = null;
  $effect(() => {
    const panel = overlayPanel;
    if (panel === focusedOverlay) return;
    focusedOverlay = panel;
    if (!panel) return;
    void tick().then(() => {
      firstFocusable(panelFor(panel))?.focus({ preventScroll: true });
    });
  });

  function opening(panel: 'left' | 'right') {
    openOrigin = panel;
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
    if (openOrigin === panel) openOrigin = null;
    await tick();
    triggerFor(panel)?.focus({ preventScroll: true });
  }

  /** Escape/scrim target: the modal drawer, else the last opened dock. */
  function activePanel(): 'left' | 'right' | null {
    return overlayPanel ?? openOrigin ?? (leftOpen ? 'left' : rightOpen ? 'right' : null);
  }

  /**
   * `inert` removes the background from the tab order, but the browser chrome
   * sits between the last control and the first one, so the drawer also wraps
   * Tab/Shift+Tab at its own boundaries. Together they are the focus trap.
   */
  function trapTab(event: KeyboardEvent) {
    const panel = overlayPanel ? panelFor(overlayPanel) : null;
    if (!panel) return;
    const focusables = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (element) => element.tabIndex >= 0 && element.getClientRects().length > 0,
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;

    if (!active || !panel.contains(active)) {
      event.preventDefault();
      first.focus({ preventScroll: true });
      return;
    }
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus({ preventScroll: true });
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus({ preventScroll: true });
    }
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
      openOrigin = null;
      // No focus move needed: the focused element stays rendered and visible.
      return;
    }

    // Docked panel → overlay. Collapsing the docks removes the focused element
    // from the layout, so deliberately restore focus to the panel's trigger.
    leftOpen = false;
    rightOpen = false;
    openOrigin = null;
    void tick().then(() => {
      if (focusInLeft) leftButton?.focus({ preventScroll: true });
      else if (focusInRight) rightButton?.focus({ preventScroll: true });
    });
  }

  onMount(() => {
    const wide = window.matchMedia('(min-width: 1200px)');
    const system = window.matchMedia('(prefers-color-scheme: dark)');

    preference = readStoredTheme(window.localStorage);
    systemDark = system.matches;
    docked = wide.matches;
    leftOpen = wide.matches;
    syncTheme();
    hydrated = true;

    const onSchemeChange = (event: MediaQueryListEvent) => {
      systemDark = event.matches;
      if (preference === 'system') syncTheme();
    };
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Tab') {
        trapTab(event);
        return;
      }
      if (event.key !== 'Escape') return;
      const panel = activePanel();
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

    <main class="stage" id="voice-stage" inert={backgroundInert ? true : undefined}>
      {#if stage}{@render stage()}{/if}
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
