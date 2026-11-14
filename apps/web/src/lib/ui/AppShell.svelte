<script lang="ts">
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';
  import './shell.css';

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
    right,
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
