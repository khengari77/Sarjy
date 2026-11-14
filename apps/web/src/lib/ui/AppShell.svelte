<script lang="ts">
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';
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
  let openOrigin = $state<'left' | 'right' | null>(null);

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

  function toggleLeft() {
    if (leftOpen) {
      leftOpen = false;
      if (openOrigin === 'left') openOrigin = null;
    } else {
      opening('left');
    }
  }

  function toggleRight() {
    if (rightOpen) {
      rightOpen = false;
      if (openOrigin === 'right') openOrigin = null;
    } else {
      opening('right');
    }
  }

  function closePanels() {
    const origin = openOrigin;
    leftOpen = false;
    rightOpen = false;
    openOrigin = null;
    (origin === 'right' ? rightButton : origin === 'left' ? leftButton : null)?.focus();
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

    const onViewportChange = (event: MediaQueryListEvent) => {
      docked = event.matches;
      leftOpen = event.matches;
      rightOpen = false;
      openOrigin = null;
    };
    const onSchemeChange = (event: MediaQueryListEvent) => {
      systemDark = event.matches;
      if (preference === 'system') syncTheme();
    };
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && (leftOpen || rightOpen)) closePanels();
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
  <header class="topbar">
    <div class="brand-row">
      <button
        bind:this={leftButton}
        class="icon-button"
        aria-label={dir === 'rtl' ? 'إظهار المحادثات' : 'Show conversations'}
        aria-expanded={leftOpen}
        aria-controls="shell-left"
        onclick={toggleLeft}
      >
        <span aria-hidden="true">☰</span>
      </button>
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
      <button
        bind:this={rightButton}
        class="icon-button"
        aria-label={dir === 'rtl' ? 'إظهار الذاكرة والإعدادات' : 'Show memory and settings'}
        aria-expanded={rightOpen}
        aria-controls="shell-right"
        onclick={toggleRight}
      >
        <span aria-hidden="true">☷</span>
      </button>
    </div>
  </header>

  <div class="body">
    {#if left}
      <aside
        class="sidebar sidebar-left"
        id="shell-left"
        data-open={leftOpen}
        data-hydrated={hydrated}
        aria-label={leftLabel}
        inert={!leftOpen ? true : undefined}
      >
        {@render left()}
      </aside>
    {/if}

    <main class="stage" id="voice-stage">
      {#if stage}{@render stage()}{/if}
    </main>

    {#if right}
      <aside
        class="sidebar sidebar-right"
        id="shell-right"
        data-open={rightOpen}
        aria-label={rightLabel}
        inert={!rightOpen ? true : undefined}
      >
        {@render right()}
      </aside>
    {/if}
  </div>

  {#if leftOpen || rightOpen}
    <button class="scrim" aria-label="Close panels" tabindex="-1" onclick={closePanels}></button>
  {/if}
</div>
