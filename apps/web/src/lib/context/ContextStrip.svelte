<script lang="ts">
  import '../memory/memory.css';
  import BudgetMeter from './BudgetMeter.svelte';
  import MindChips from './MindChips.svelte';
  import { createContextStrip } from './strip.svelte';

  interface Props {
    conversationId: string | null;
    /** Same finalised-turn signal the memory explorer refreshes on. */
    revision?: string;
  }

  let { conversationId, revision = '' }: Props = $props();

  const strip = createContextStrip({
    conversationId: () => conversationId,
    revision: () => revision,
  });
</script>

<section class="mind-strip" aria-label="In Sarjy's context" data-testid="mind-strip">
  <header class="mind-strip-head">
    <h3>In Sarjy's context</h3>
    {#if strip.budget}
      <BudgetMeter budget={strip.budget} label="Context budget" compact />
    {/if}
  </header>

  {#if strip.loading && !strip.data}
    <p class="memory-muted" role="status">Reading the compiler's context…</p>
  {:else if strip.error}
    <div class="memory-error-block" role="alert">
      <p>{strip.error}</p>
      <button class="ghost-button" onclick={() => void strip.retry()}>Retry</button>
    </div>
  {:else if !conversationId}
    <p class="memory-muted">Select a conversation to see what Sarjy can carry into the next reply.</p>
  {:else if strip.chips.length === 0}
    <p class="memory-muted">No memory blocks are in this conversation's compiled context yet.</p>
  {:else}
    <MindChips chips={strip.chips} />
  {/if}
</section>

<style>
  .mind-strip {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    background: color-mix(in srgb, var(--orb-memory) 7%, transparent);
  }

  .mind-strip-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }

  .mind-strip-head h3 {
    margin: 0;
    font-size: 13px;
    letter-spacing: 0.02em;
  }

  .mind-strip-head :global(.budget-meter) {
    flex: 1 1 150px;
    max-width: 220px;
  }

  .mind-strip .ghost-button {
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

  .mind-strip .ghost-button:hover {
    background: var(--surface-hover);
  }
</style>
