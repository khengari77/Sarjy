<script lang="ts">
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';

  interface Props {
    dir?: 'ltr' | 'rtl';
    modeLabel?: string;
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
    modeLabel = 'Simple',
    languageLabel = 'العربية',
    onLanguage,
    leftLabel = 'Conversations',
    rightLabel = 'Memory and settings',
    left,
    stage,
    right
  }: Props = $props();

  let leftOpen = $state(true);
  let rightOpen = $state(true);

  onMount(() => {
    const wide = window.matchMedia('(min-width: 1200px)').matches;
    leftOpen = wide;
    rightOpen = wide;
  });
</script>

<div class="shell" {dir}>
  <header class="topbar">
    <div class="brand-row">
      <button
        class="icon-button"
        aria-label={dir === 'rtl' ? 'إظهار المحادثات' : 'Show conversations'}
        aria-expanded={leftOpen}
        onclick={() => (leftOpen = !leftOpen)}
      >
        <span aria-hidden="true">☰</span>
      </button>
      <strong class="brand">سَرْجي <span>SARJY</span></strong>
    </div>
    <div class="header-actions">
      <span class="mode-pill">{modeLabel}</span>
      {#if onLanguage}
        <button class="ghost-button" onclick={onLanguage} lang={dir === 'rtl' ? 'en' : 'ar'}>
          {languageLabel}
        </button>
      {/if}
      <button
        class="icon-button"
        aria-label={dir === 'rtl' ? 'إظهار الذاكرة والإعدادات' : 'Show memory and settings'}
        aria-expanded={rightOpen}
        onclick={() => (rightOpen = !rightOpen)}
      >
        <span aria-hidden="true">☷</span>
      </button>
    </div>
  </header>

  <div class="body">
    {#if left}
      <aside class="sidebar sidebar-left" data-open={leftOpen} aria-label={leftLabel}>
        {@render left()}
      </aside>
    {/if}

    <main class="stage" id="voice-stage">
      {#if stage}{@render stage()}{/if}
    </main>

    {#if right}
      <aside class="sidebar sidebar-right" data-open={rightOpen} aria-label={rightLabel}>
        {@render right()}
      </aside>
    {/if}
  </div>

  {#if leftOpen || rightOpen}
    <button
      class="scrim"
      aria-label="Close panels"
      tabindex="-1"
      onclick={() => {
        leftOpen = false;
        rightOpen = false;
      }}
    ></button>
  {/if}
</div>

<style>
  .shell {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
    min-height: 100dvh;
    background: var(--bg-app);
    color: var(--text-primary);
  }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 20px;
    padding-top: max(12px, env(safe-area-inset-top));
    border-bottom: 1px solid var(--border-subtle);
    background: color-mix(in srgb, var(--bg-app) 88%, transparent);
    backdrop-filter: blur(8px);
    position: sticky;
    top: 0;
    z-index: 20;
  }

  .brand-row,
  .header-actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .brand {
    font-size: 20px;
    letter-spacing: 0.06em;
    color: var(--text-primary);
  }

  .brand span {
    font-size: 10px;
    letter-spacing: 0.3em;
    color: var(--text-muted);
  }

  .mode-pill {
    font-size: 11px;
    letter-spacing: 0.08em;
    padding: 5px 10px;
    border-radius: 999px;
    border: 1px solid var(--border-subtle);
    color: var(--text-muted);
    text-transform: uppercase;
  }

  .icon-button,
  .ghost-button {
    font: inherit;
    border-radius: var(--radius-control);
    min-height: var(--control-height);
    padding: 10px 16px;
    border: 1px solid var(--border-subtle);
    color: var(--text-primary);
    background: transparent;
    cursor: pointer;
    transition:
      background var(--transition-quick),
      border-color var(--transition-quick);
  }

  .icon-button {
    min-width: var(--control-height);
    padding: 8px;
    font-size: 18px;
    line-height: 1;
  }

  .ghost-button:hover,
  .icon-button:hover {
    background: var(--surface-hover);
  }

  .body {
    flex: 1;
    display: flex;
    min-height: 0;
  }

  .sidebar {
    background: var(--bg-sidebar);
    border-inline-end: 1px solid var(--border-subtle);
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow-y: auto;
  }

  .sidebar-left {
    width: var(--sidebar-left-width);
    flex: 0 0 auto;
  }

  .sidebar-right {
    width: var(--sidebar-right-width);
    flex: 0 0 auto;
    border-inline-end: none;
    border-inline-start: 1px solid var(--border-subtle);
  }

  .stage {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    position: relative;
  }

  .scrim {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 25;
    border: 0;
    padding: 0;
    background: rgb(2 5 10 / 62%);
    cursor: pointer;
  }

  /* Medium: sidebars become overlay drawers. */
  @media (max-width: 1199px) {
    .sidebar {
      position: fixed;
      top: 0;
      bottom: 0;
      z-index: 30;
      transform: translateX(-102%);
      transition: transform var(--transition-state);
      max-width: min(88vw, var(--sidebar-right-width));
      box-shadow: 0 0 40px rgb(0 0 0 / 45%);
    }

    .sidebar-right {
      inset-inline-end: 0;
      inset-inline-start: auto;
      transform: translateX(102%);
    }

    .sidebar-left {
      inset-inline-start: 0;
    }

    .sidebar[data-open='true'] {
      transform: translateX(0);
    }

    .scrim {
      display: block;
    }
  }

  @media (max-width: 767px) {
    .sidebar-left {
      width: min(86vw, 320px);
    }

    .sidebar-right {
      width: min(92vw, 360px);
    }

    .mode-pill {
      display: none;
    }
  }

  @media (min-width: 1200px) {
    .sidebar-left[data-open='false'],
    .sidebar-right[data-open='false'] {
      display: none;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .sidebar {
      transition: none;
    }
  }
</style>
