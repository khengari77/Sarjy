<script lang="ts">
  import type { Turn } from '../api';
  import ToolTraceChip from './ToolTraceChip.svelte';

  interface Props {
    open: boolean;
    turns: Turn[];
    tools: { id: string; name: string; result: unknown }[];
    arabic: boolean;
    onclose: () => void;
  }

  let { open, turns, tools, arabic, onclose }: Props = $props();

  let closeButton: HTMLButtonElement | undefined = $state();

  $effect(() => {
    if (open) closeButton?.focus();
  });

  function onkeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={open ? onkeydown : undefined} />

{#if open}
  <section class="drawer" aria-label={arabic ? 'سجل المحادثة' : 'Conversation transcript'}>
    <header class="drawer-head">
      <h2>{arabic ? 'المحادثة' : 'Transcript'}</h2>
      <button class="close" bind:this={closeButton} type="button" onclick={onclose} aria-label={arabic ? 'إغلاق السجل' : 'Close transcript'}>
        ✕
      </button>
    </header>
    <div class="drawer-body">
      {#if turns.length === 0}
        <p class="empty">{arabic ? 'لا توجد رسائل بعد.' : 'No messages yet.'}</p>
      {:else}
        {#each turns as turn}
          <article class="turn" class:assistant={turn.role === 'assistant'} dir="auto">
            <small>{turn.role} · {turn.status}</small>
            <p>{turn.text}</p>
          </article>
        {/each}
      {/if}
      {#if tools.length}
        <div class="traces">
          {#each tools as tool}
            <ToolTraceChip name={tool.name} {arabic} />
          {/each}
        </div>
      {/if}
    </div>
  </section>
{/if}

<style>
  .drawer {
    position: fixed;
    inset-block: 64px 0;
    inset-inline-end: 0;
    width: min(92vw, 420px);
    z-index: 40;
    display: flex;
    flex-direction: column;
    background: var(--bg-sidebar);
    border-inline-start: 1px solid var(--border-subtle);
    box-shadow: -20px 0 50px rgb(0 0 0 / 45%);
  }

  .drawer-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .drawer-head h2 {
    margin: 0;
    font-size: 12px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  .close {
    font: inherit;
    font-size: 16px;
    min-width: 44px;
    min-height: 44px;
    border-radius: var(--radius-control);
    border: 1px solid var(--border-subtle);
    background: transparent;
    color: var(--text-primary);
    cursor: pointer;
  }

  .close:hover {
    background: var(--surface-hover);
  }

  .drawer-body {
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .turn {
    text-align: start;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-panel);
    padding: 12px 14px;
  }

  .turn.assistant {
    background: color-mix(in srgb, var(--surface-elevated) 70%, var(--bg-app));
  }

  .turn small {
    color: var(--text-muted);
    font-size: 11px;
    letter-spacing: 0.04em;
  }

  .turn p {
    margin: 6px 0 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 14px;
  }

  .traces {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .empty {
    color: var(--text-muted);
    font-size: 14px;
  }

  @media (max-width: 767px) {
    .drawer {
      inset-block: auto max(0px, env(safe-area-inset-bottom)) 0;
      inset-inline: 0;
      width: 100%;
      height: min(70vh, 560px);
      border-inline-start: 0;
      border-top: 1px solid var(--border-subtle);
      border-radius: var(--radius-panel) var(--radius-panel) 0 0;
      box-shadow: 0 -20px 50px rgb(0 0 0 / 45%);
    }
  }
</style>
