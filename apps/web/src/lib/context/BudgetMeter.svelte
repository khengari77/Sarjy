<script lang="ts">
  import { meterValues, type ContextBudget } from './context';

  interface Props {
    budget: ContextBudget;
    label: string;
    arabic?: boolean;
    compact?: boolean;
  }

  let { budget, label, arabic = false, compact = false }: Props = $props();

  const values = $derived(meterValues(budget));
  const format = (value: number) => value.toLocaleString('en');
</script>

<div
  class="budget-meter"
  class:compact
  role="meter"
  aria-label={label}
  aria-valuenow={values.now}
  aria-valuemin={values.min}
  aria-valuemax={values.max}
>
  <div class="budget-track" aria-hidden="true">
    <span class="budget-fill" style:width={`${values.percent}%`}></span>
  </div>
  <p class="budget-text">
    {format(values.used)} / {format(values.limit)}
    {arabic ? 'حرفًا' : 'characters'}{#if values.over}<span class="budget-over">
        · {arabic ? 'تجاوز الميزانية' : 'over budget'}</span
      >{/if}
  </p>
</div>

<style>
  .budget-meter {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .budget-track {
    height: 6px;
    overflow: hidden;
    border-radius: 999px;
    background: color-mix(in srgb, var(--text-muted) 22%, transparent);
  }

  .budget-fill {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--orb-idle);
    transition: width var(--transition-quick);
  }

  .budget-meter:not(.compact) .budget-track {
    height: 8px;
  }

  .budget-text {
    margin: 0;
    color: var(--text-muted);
    font-size: 11px;
    line-height: 1.35;
  }

  .budget-over {
    color: var(--orb-confirmation);
    font-weight: 600;
  }
</style>
