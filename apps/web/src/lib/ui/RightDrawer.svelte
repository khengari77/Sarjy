<script lang="ts">
  import type { Snippet } from 'svelte';
  import DrawerCloseButton from './DrawerCloseButton.svelte';

  /**
   * Three panels, one concern each:
   *  - Settings: every interactive setting (voice, speed, captions).
   *  - Memory: the memory/context search and explorer.
   *  - Transcript: the full conversation, opened from the caption bubble.
   * Appearance was removed: its theme control lives in the header and captions
   * moved into Settings, so the panel would have been empty. The Context
   * inspector was removed by owner decision (2026-11-15): the compiled-context
   * blocks belong to the memory store, not to a fourth tab.
   */
  export type TabId = 'memories' | 'controls' | 'transcript';

  interface Tab {
    id: TabId;
    label: string;
  }

  interface Props {
    arabic?: boolean;
    /** Functional settings stay reachable in the Settings tab. */
    voicePanel?: Snippet;
    /** Transparent memory/context explorer (#43). Rendered in its own tab;
     *  the argument is true while that tab is visible. */
    memoryPanel?: Snippet<[boolean]>;
    /** Full conversation transcript. */
    transcriptPanel?: Snippet;
    /** Selected tab, bindable so command navigation can choose it. */
    activeTab?: TabId;
  }

  let {
    arabic = false,
    voicePanel,
    memoryPanel,
    transcriptPanel,
    activeTab = $bindable<TabId>('controls'),
  }: Props = $props();

  const tabs: Tab[] = [
    { id: 'memories', label: 'Memory' },
    { id: 'controls', label: 'Settings' },
    { id: 'transcript', label: 'Transcript' },
  ];

  let tablist = $state<HTMLDivElement | null>(null);

  function focusTab(index: number) {
    const buttons = tablist?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    if (!buttons?.length) return;
    const next = buttons[(index + buttons.length) % buttons.length];
    next.focus();
    next.click();
  }

  function onTabKeydown(event: KeyboardEvent, index: number) {
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      focusTab(index + 1);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      focusTab(index - 1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusTab(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusTab(tabs.length - 1);
    }
  }
</script>

<div class="drawer" dir={arabic ? 'rtl' : 'ltr'}>
  <div class="drawer-top">
    <DrawerCloseButton {arabic} panel="right" />
    <div
      class="tabs"
      role="tablist"
      aria-label={arabic ? 'ألواح الذاكرة والإعدادات' : 'Memory and settings panels'}
      bind:this={tablist}
    >
      {#each tabs as tab, index (tab.id)}
        <button
          type="button"
          role="tab"
          id={`drawer-tab-${tab.id}`}
          aria-selected={activeTab === tab.id}
          aria-controls={`drawer-panel-${tab.id}`}
          tabindex={activeTab === tab.id ? 0 : -1}
          class="tab"
          onclick={() => (activeTab = tab.id)}
          onkeydown={(event) => onTabKeydown(event, index)}
        >
          {tab.label}
        </button>
      {/each}
    </div>
  </div>

  {#each tabs as tab (tab.id)}
    <!-- Every tab owns a real panel: all three ids exist in the DOM so each
         tab's aria-controls resolves. Only the selected panel is shown. -->
    <div
      class="panel"
      role="tabpanel"
      id={`drawer-panel-${tab.id}`}
      aria-labelledby={`drawer-tab-${tab.id}`}
      tabindex={activeTab === tab.id ? 0 : -1}
      hidden={activeTab !== tab.id}
    >
      {#if tab.id === 'controls'}
        {#if voicePanel}
          {@render voicePanel()}
        {:else}
          <p class="placeholder">Voice controls are unavailable.</p>
        {/if}
      {:else if tab.id === 'memories'}
        {#if memoryPanel}
          {@render memoryPanel(activeTab === 'memories')}
        {:else}
          <section class="placeholder-block">
            <h2>Memory</h2>
            <p>No remembered items are shown yet.</p>
          </section>
        {/if}
      {:else if tab.id === 'transcript'}
        {#if transcriptPanel}
          {@render transcriptPanel()}
        {:else}
          <p class="placeholder">No messages yet.</p>
        {/if}
      {/if}
    </div>
  {/each}
</div>

<style>
  .drawer {
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }

  /* Close first in the DOM and at the inline end visually: row-reverse keeps
     the tab order and the reading order identical to the focus order. */
  .drawer-top {
    position: sticky;
    top: 0;
    z-index: 1;
    display: flex;
    flex-direction: row-reverse;
    align-items: center;
    gap: 4px;
    padding: max(6px, env(safe-area-inset-top)) 8px 6px;
    background: var(--bg-sidebar);
    border-bottom: 1px solid var(--border-subtle);
  }

  .tabs {
    display: flex;
    flex: 1;
    gap: 2px;
    min-width: 0;
  }

  .tab {
    font: inherit;
    flex: 1 1 auto;
    font-size: 12px;
    letter-spacing: 0.02em;
    min-height: 36px;
    padding: 6px 8px;
    border: 1px solid transparent;
    border-radius: var(--radius-pill);
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    white-space: nowrap;
    transition:
      background var(--transition-quick),
      color var(--transition-quick),
      border-color var(--transition-quick);
  }

  @media (pointer: coarse) {
    .tab {
      min-height: var(--min-tap-target);
    }
  }

  .tab:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .tab[aria-selected='true'] {
    background: var(--surface-elevated);
    border-color: var(--border-subtle);
    color: var(--text-primary);
  }

  .panel {
    flex: 1;
    min-height: 0;
    padding: 16px;
  }

  .placeholder-block h2 {
    margin: 0 0 6px;
    font-size: 14px;
    letter-spacing: 0.02em;
  }

  .placeholder-block p,
  .placeholder {
    margin: 0 0 8px;
    color: var(--text-muted);
    font-size: 13px;
    line-height: 1.5;
  }
</style>
