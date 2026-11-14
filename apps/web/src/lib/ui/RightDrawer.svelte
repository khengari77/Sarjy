<script lang="ts">
  import type { Snippet } from 'svelte';

  type TabId = 'memories' | 'voice' | 'appearance' | 'context';

  interface Tab {
    id: TabId;
    label: string;
  }

  interface Props {
    arabic?: boolean;
    /** Existing voice controls stay reachable in the Voice tab (#41 owns their contents). */
    voicePanel?: Snippet;
    /** Transparent memory/context explorer (#43). Rendered in its own tab. */
    memoryPanel?: Snippet;
  }

  let { arabic = false, voicePanel, memoryPanel }: Props = $props();

  const tabs: Tab[] = [
    { id: 'memories', label: 'Memories' },
    { id: 'voice', label: 'Voice' },
    { id: 'appearance', label: 'Appearance' },
    { id: 'context', label: 'Context' },
  ];

  let active = $state<TabId>('voice');
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
        aria-selected={active === tab.id}
        aria-controls={`drawer-panel-${tab.id}`}
        tabindex={active === tab.id ? 0 : -1}
        class="tab"
        onclick={() => (active = tab.id)}
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
      tabindex={active === tab.id ? 0 : -1}
      hidden={active !== tab.id}
    >
      {#if tab.id === 'voice'}
        {#if voicePanel}
          {@render voicePanel()}
        {:else}
          <p class="placeholder">Voice settings arrive in #46.</p>
        {/if}
      {:else if tab.id === 'appearance'}
        <section class="placeholder-block">
          <h2>Appearance</h2>
          <p>Theme is available from the header control. Further appearance options arrive in #46.</p>
        </section>
        <section class="placeholder-block">
          <h2>Workspace mode</h2>
          <p>
            Simple and Developer modes move here once the switch ships in #46. The header keeps only controls that do
            something.
          </p>
          <p class="mode-preview" aria-hidden="true"><span>Simple</span><span>Developer</span></p>
        </section>
      {:else if tab.id === 'memories'}
        {#if memoryPanel}
          {@render memoryPanel()}
        {:else}
          <section class="placeholder-block">
            <h2>Memories</h2>
            <p>No remembered items are shown yet.</p>
          </section>
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
    gap: 4px;
    padding: 10px 12px;
    background: var(--bg-sidebar);
    border-bottom: 1px solid var(--border-subtle);
    overflow-x: auto;
  }

  .tab {
    font: inherit;
    font-size: 12px;
    letter-spacing: 0.04em;
    min-height: 36px;
    padding: 6px 10px;
    border: 1px solid transparent;
    border-radius: var(--radius-control);
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

  .placeholder-block + .placeholder-block {
    margin-top: 20px;
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

  .mode-preview {
    display: inline-flex;
    gap: 4px;
    padding: 4px;
    border: 1px dashed var(--border-subtle);
    border-radius: 999px;
    font-size: 11px;
  }

  .mode-preview span {
    padding: 3px 10px;
    border-radius: 999px;
  }

  .mode-preview span:first-child {
    background: var(--surface-hover);
    color: var(--text-primary);
  }
</style>
