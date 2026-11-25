<script lang="ts">
  import '../memory/memory.css';
  import BudgetMeter from './BudgetMeter.svelte';
  import { friendlyBlocksError, loadContextBlocks, memoryChips, type ContextBlocks } from './context';

  interface Props {
    conversationId: string | null;
    /** Same finalised-turn signal the memory explorer refreshes on. */
    revision?: string;
  }

  let { conversationId, revision = '' }: Props = $props();

  let data = $state<ContextBlocks | null>(null);
  let loading = $state(false);
  let error = $state('');
  let nonce = $state(0);
  let revealed = $state<Record<string, boolean>>({});
  let lastKey = '';
  let generation = 0;

  function toggleReason(id: string) {
    revealed = { ...revealed, [id]: !revealed[id] };
  }

  $effect(() => {
    const key = `${conversationId ?? ''}:${revision}:${nonce}`;
    if (key === lastKey) return;
    lastKey = key;
    const conversation = conversationId;
    const request = ++generation;
    if (!conversation) {
      data = null;
      loading = false;
      error = '';
      return;
    }
    loading = true;
    error = '';
    void loadContextBlocks(conversation).then(
      (result) => {
        if (request !== generation) return;
        data = result;
        loading = false;
      },
      () => {
        if (request !== generation) return;
        data = null;
        loading = false;
        error = friendlyBlocksError();
      },
    );
  });

  const chips = $derived(data ? memoryChips(data.blocks) : []);
  const budget = $derived(data?.budget);
</script>

<section class="mind-strip" aria-label="In Sarjy's context" data-testid="mind-strip">
  <header class="mind-strip-head">
    <h3>In Sarjy's context</h3>
    {#if budget}
      <BudgetMeter {budget} label="Context budget" compact />
    {/if}
  </header>

  {#if loading && !data}
    <p class="memory-muted" role="status">Reading the compiler's context…</p>
  {:else if error}
    <div class="memory-error-block" role="alert">
      <p>{error}</p>
      <button class="ghost-button" onclick={() => (nonce += 1)}>Retry</button>
    </div>
  {:else if !conversationId}
    <p class="memory-muted">Select a conversation to see what Sarjy can carry into the next reply.</p>
  {:else if chips.length === 0}
    <p class="memory-muted">No memory blocks are in this conversation's compiled context yet.</p>
  {:else}
    <ul class="mind-chips">
      {#each chips as chip (chip.id)}
        <li>
          <button
            type="button"
            class="mind-chip"
            data-state={chip.state}
            aria-expanded={revealed[chip.id] === true}
            title={chip.accessible}
            aria-label={chip.accessible}
            onclick={() => toggleReason(chip.id)}
          >
            <span class="mind-chip-text">{chip.text || chip.label}</span>
            <span class="mind-chip-state">{chip.label}</span>
            {#if chip.reason}
              <span class="mind-reason" aria-hidden="true">{chip.reason}</span>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
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

  .mind-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .mind-chip {
    position: relative;
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    max-width: 260px;
    padding: 4px 9px;
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    background: var(--surface-elevated);
    color: var(--text-primary);
    font: inherit;
    font-size: 11px;
    line-height: 1.3;
    text-align: start;
    cursor: pointer;
  }

  .mind-chip:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .mind-chip[data-state='included'] {
    border-color: color-mix(in srgb, var(--orb-idle) 60%, var(--border-subtle));
  }

  .mind-chip[data-state='hidden'],
  .mind-chip[data-state='forgotten'],
  .mind-chip[data-state='not_included'] {
    border-color: color-mix(in srgb, var(--orb-confirmation) 55%, var(--border-subtle));
    color: var(--text-muted);
  }

  .mind-chip[data-state='over_budget'] {
    border-style: dashed;
    color: var(--text-muted);
  }

  .mind-chip-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mind-chip-state {
    flex-shrink: 0;
    color: var(--text-muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .mind-chip[data-state='included'] .mind-chip-state {
    color: var(--orb-idle);
  }

  .mind-reason {
    position: absolute;
    inset-inline-start: 0;
    top: calc(100% + 4px);
    z-index: 2;
    display: none;
    width: max-content;
    max-width: 240px;
    padding: 5px 8px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    background: var(--surface-raised);
    color: var(--text-primary);
    box-shadow: 0 6px 18px rgb(0 0 0 / 0.28);
  }

  .mind-chip:hover .mind-reason,
  .mind-chip:focus-visible .mind-reason,
  .mind-chip[aria-expanded='true'] .mind-reason {
    display: block;
  }
</style>
