<script lang="ts">
  import type { ContextChip } from './mind';

  interface Props {
    chips: ContextChip[];
  }

  let { chips }: Props = $props();

  let revealed = $state<Record<string, boolean>>({});

  function toggleReason(id: string) {
    revealed = { ...revealed, [id]: !revealed[id] };
  }
</script>

<ul class="mind-chips">
  {#each chips as chip (chip.id)}
    <li>
      <button
        type="button"
        class="mind-chip"
        data-state={chip.state}
        data-retraction={chip.retraction ? 'true' : undefined}
        aria-expanded={revealed[chip.id] === true}
        title={chip.tooltip ?? chip.accessible}
        aria-label={chip.accessible}
        onclick={() => toggleReason(chip.id)}
      >
        {#if chip.retraction}
          <span class="mind-chip-text">{chip.label}</span>
        {:else}
          <span class="mind-chip-text">{chip.text || chip.label}</span>
          <span class="mind-chip-state">{chip.label}</span>
        {/if}
        {#if chip.reason}
          <span class="mind-reason" aria-hidden="true">{chip.reason}</span>
        {/if}
      </button>
    </li>
  {/each}
</ul>

<style>
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
  .mind-chip[data-state='deleted'],
  .mind-chip[data-state='replaced'],
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
