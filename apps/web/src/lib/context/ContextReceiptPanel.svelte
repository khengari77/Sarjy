<script lang="ts">
  import '../memory/memory.css';
  import { shortId } from '../memory/memory';
  import BudgetMeter from './BudgetMeter.svelte';
  import { contextKindLabel, diffLabel, humanReason, type TurnContext } from './context';
  import { contextWords } from './copy';

  interface Props {
    data: TurnContext;
    arabic?: boolean;
  }

  let { data, arabic = false }: Props = $props();

  const words = $derived(contextWords(arabic));
  const shown = $derived(data.included.filter((item) => item.kind !== 'history'));
  const added = $derived(data.diff.added.map((id) => ({ id, label: diffLabel(id, data.included, data.excluded) })));
  const removed = $derived(data.diff.removed.map((id) => ({ id, label: diffLabel(id, data.included, data.excluded) })));
</script>

<section class="context-receipt" aria-label={words.why}>
  <BudgetMeter budget={data.budget} label={words.budget} {arabic} />
  <p class="memory-muted">{words.history(data.history_count)}</p>

  {#if shown.length}
    <h4>{words.included}</h4>
    <ul class="receipt-items">
      {#each shown as item (item.id)}
        <li class="receipt-item">
          <div class="receipt-item-meta">
            <span class="chip">{contextKindLabel(item.kind)}</span>
            <span class="chip receipt-data">{words.dataBadge}</span>
          </div>
          <p class="receipt-text">{item.text}</p>
        </li>
      {/each}
    </ul>
  {/if}

  {#if data.excluded.length}
    <h4>{words.excluded}</h4>
    <ul class="receipt-excluded">
      {#each data.excluded as item (item.id)}
        <li><code>{shortId(item.id)}</code><span>{humanReason(item.reason)}</span></li>
      {/each}
    </ul>
  {/if}

  <div class="memory-diff receipt-diff">
    <h4>{words.diff}</h4>
    {#if data.previous_turn_id === null}
      <p class="memory-diff-summary">{words.firstTurn}</p>
    {:else if added.length === 0 && removed.length === 0}
      <p class="memory-diff-summary">{words.noDiff}</p>
    {:else}
      {#if added.length}
        <p class="memory-diff-label">{words.added}</p>
        <ul>
          {#each added as entry (entry.id)}
            <li><span>{entry.label}</span></li>
          {/each}
        </ul>
      {/if}
      {#if removed.length}
        <p class="memory-diff-label">{words.removed}</p>
        <ul>
          {#each removed as entry (entry.id)}
            <li><span>{entry.label}</span></li>
          {/each}
        </ul>
      {/if}
    {/if}
  </div>

  <p class="receipt-hash">
    <span>{words.promptHash}</span>
    <code>{data.prompt_sha256.slice(0, 12)}</code>
  </p>
  <p class="memory-muted">{words.sameHash}</p>
</section>

<style>
  .context-receipt {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: 10px;
    padding: 12px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    background: color-mix(in srgb, var(--surface-raised) 70%, transparent);
    font-size: 12px;
  }

  .context-receipt h4 {
    margin: 0;
    font-size: 12px;
    letter-spacing: 0.02em;
  }

  .receipt-items,
  .receipt-excluded {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .receipt-item {
    padding: 8px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    background: var(--surface-elevated);
  }

  .receipt-item-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }

  .receipt-data {
    border-color: color-mix(in srgb, var(--orb-memory) 65%, transparent);
    color: var(--orb-memory);
  }

  .receipt-text {
    margin: 6px 0 0;
    line-height: 1.45;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
  }

  .receipt-excluded li {
    display: flex;
    gap: 8px;
    align-items: baseline;
    color: var(--text-muted);
  }

  .receipt-diff {
    margin-top: 2px;
  }

  .receipt-hash {
    display: flex;
    align-items: baseline;
    gap: 6px;
    margin: 0;
    color: var(--text-muted);
  }

  .receipt-hash code {
    color: var(--text-primary);
    font-size: 12px;
  }
</style>
