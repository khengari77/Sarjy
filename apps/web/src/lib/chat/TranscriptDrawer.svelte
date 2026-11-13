<script lang="ts">
  import type { Turn } from '../api';
  import ToolTraceChip from './ToolTraceChip.svelte';

  interface Props {
    open: boolean;
    turns: Turn[];
    tools: { id: string; name: string; result: unknown; turn_id?: string }[];
    arabic: boolean;
    provisional?: { id: string; text: string; speaker: 'user' | 'assistant' } | null;
    onclose: () => void;
  }

  let { open, turns, tools, arabic, provisional = null, onclose }: Props = $props();

  let closeButton: HTMLButtonElement | undefined = $state();

  const byTurn = $derived.by(() => {
    const grouped = new Map<string, { id: string; name: string; result: unknown; turn_id?: string }[]>();
    for (const tool of tools) {
      if (!tool.turn_id) continue;
      const bucket = grouped.get(tool.turn_id) ?? [];
      bucket.push(tool);
      grouped.set(tool.turn_id, bucket);
    }
    return grouped;
  });
  const unattached = $derived(tools.filter((tool) => !tool.turn_id || !turns.some((turn) => turn.id === tool.turn_id)));

  // Human wording only: role/status values come from the API and must never
  // read as raw enums in the interface.
  function speaker(role: string): string {
    if (role === 'user') return arabic ? 'أنت' : 'You';
    if (role === 'assistant') return arabic ? 'سَرْجي' : 'Sarjy';
    return arabic ? 'مشارك' : 'Participant';
  }

  function statusText(status: string): string {
    if (status === 'failed' || status === 'error') return arabic ? 'لم يكتمل' : 'not completed';
    return '';
  }

  $effect(() => {
    if (open) closeButton?.focus();
  });

  function onkeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={open ? onkeydown : undefined} />

{#if open}
  <div
    class="drawer"
    id="transcript-drawer"
    role="dialog"
    aria-modal="false"
    aria-label={arabic ? 'سجل المحادثة' : 'Conversation transcript'}
  >
    <header class="drawer-head">
      <h2>{arabic ? 'المحادثة' : 'Transcript'}</h2>
      <button
        class="close"
        bind:this={closeButton}
        type="button"
        onclick={onclose}
        aria-label={arabic ? 'إغلاق السجل' : 'Close transcript'}
      >
        ✕
      </button>
    </header>
    <div class="drawer-body">
      {#if turns.length === 0 && !provisional}
        <p class="empty">{arabic ? 'لا توجد رسائل بعد.' : 'No messages yet.'}</p>
      {:else}
        {#each turns as turn (turn.id)}
          <article class="turn" class:assistant={turn.role === 'assistant'} dir="auto">
            <small
              >{speaker(turn.role)}{#if statusText(turn.status)}
                · {statusText(turn.status)}{/if}</small
            >
            <p>{turn.text}</p>
            {#if byTurn.get(turn.id)?.length}
              <div class="traces">
                {#each byTurn.get(turn.id) ?? [] as tool (tool.id)}
                  <ToolTraceChip name={tool.name} result={tool.result} {arabic} />
                {/each}
              </div>
            {/if}
          </article>
        {/each}
      {/if}
      {#if provisional}
        <article class="turn provisional" dir="auto" aria-label={arabic ? 'نص مؤقت' : 'Provisional transcript'}>
          <small>{speaker(provisional.speaker)} · {arabic ? 'مؤقت' : 'provisional'}</small>
          <p>{provisional.text}…</p>
        </article>
      {/if}
      {#if unattached.length}
        <div class="traces">
          {#each unattached as tool (tool.id)}
            <ToolTraceChip name={tool.name} result={tool.result} {arabic} />
          {/each}
        </div>
      {/if}
    </div>
  </div>
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

  .turn.provisional {
    border-style: dashed;
    font-style: italic;
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
