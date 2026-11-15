<script lang="ts">
  import type { Snippet } from 'svelte';

  /**
   * Four panels, one concern each:
   *  - Controls: every interactive setting (voice, speed, captions).
   *  - Memories: the memory/context search and explorer.
   *  - Context: the compiled-context inspector.
   *  - Transcript: the full conversation, opened from the caption bubble.
   * Appearance was removed: its theme control lives in the header and captions
   * moved into Controls, so the panel would have been empty.
   */
  export type TabId = 'memories' | 'controls' | 'context' | 'transcript';

  interface Tab {
    id: TabId;
    label: string;
  }

  interface Props {
    arabic?: boolean;
    /** Functional settings stay reachable in the Controls tab. */
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
    { id: 'memories', label: 'Memories' },
    { id: 'controls', label: 'Controls' },
    { id: 'context', label: 'Context' },
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

  {#each tabs as tab (tab.id)}
    <!-- Every tab owns a real panel: all four ids exist in the DOM so each
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
            <h2>Memories</h2>
            <p>No remembered items are shown yet.</p>
          </section>
        {/if}
      {:else if tab.id === 'transcript'}
        {#if transcriptPanel}
          {@render transcriptPanel()}
        {:else}
          <p class="placeholder">No messages yet.</p>
        {/if}
      {:else}
        <section class="placeholder-block">
          <h2>Context</h2>
          <p>
            The general context inspector arrives in #46. Saved facts and whether each one is included in or excluded
            from the next compiled context are shown in the Memories tab.
          </p>
        </section>
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

  .tabs {
    position: sticky;
    top: 0;
    z-index: 1;
    display: flex;
    gap: 2px;
    padding: 10px 12px;
    background: var(--bg-sidebar);
    border-bottom: 1px solid var(--border-subtle);
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
