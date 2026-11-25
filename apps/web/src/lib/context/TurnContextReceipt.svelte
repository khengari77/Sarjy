<script lang="ts">
  import '../memory/memory.css';
  import ContextReceiptPanel from './ContextReceiptPanel.svelte';
  import { contextWords } from './copy';
  import type { TurnContextReceipts } from './receipt.svelte';

  interface Props {
    turnId: string;
    arabic?: boolean;
    receipts: TurnContextReceipts;
  }

  let { turnId, arabic = false, receipts }: Props = $props();

  const words = $derived(contextWords(arabic));
  const entry = $derived(receipts.entry(turnId));
  const panelId = $derived(`context-receipt-${turnId}`);

  let open = $state(false);

  function toggle() {
    open = !open;
    if (open) void receipts.open(turnId);
  }
</script>

<div class="why-answer">
  <button type="button" class="why-button" aria-expanded={open} aria-controls={panelId} onclick={toggle}>
    {words.why}
  </button>
  {#if open}
    <div id={panelId} class="why-panel" role="region" aria-label={words.why}>
      {#if entry.status === 'loading'}
        <p class="memory-muted" role="status">{words.loading}</p>
      {:else if entry.status === 'missing'}
        <p class="memory-muted" role="status">{words.missing}</p>
      {:else if entry.status === 'error'}
        <div class="memory-error-block" role="alert">
          <p>{entry.message}</p>
          <button class="ghost-button" onclick={() => void receipts.open(turnId)}>{words.retry}</button>
        </div>
      {:else if entry.data}
        <ContextReceiptPanel data={entry.data} {arabic} />
      {/if}
    </div>
  {/if}
</div>

<style>
  .why-answer {
    margin-top: 8px;
  }

  .why-button {
    font: inherit;
    min-height: 30px;
    padding: 4px 10px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-pill);
    background: transparent;
    color: var(--text-muted);
    font-size: 11px;
    cursor: pointer;
  }

  .why-button:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .why-button:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .why-panel :global(.ghost-button) {
    align-self: flex-start;
    font: inherit;
    min-height: 36px;
    padding: 6px 12px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    background: transparent;
    color: var(--text-primary);
    cursor: pointer;
  }

  .why-panel :global(.ghost-button:hover) {
    background: var(--surface-hover);
  }
</style>
