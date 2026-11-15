<script lang="ts">
  import type { DiagnosticsModel } from './panelModel';

  let { model }: { model: DiagnosticsModel } = $props();
</script>

<div class="sections">
  {#each model.sections as section (section.id)}
    <article class="dev-section" data-section={section.id} data-status={section.status}>
      <h3>{section.title}</h3>
      {#if section.rows.length}
        <dl>
          {#each section.rows as row, index (section.id + '-' + index)}
            <div class="row">
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          {/each}
        </dl>
      {/if}
      <p class="note">{section.note}</p>
    </article>
  {/each}
</div>

<style>
  .sections {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .dev-section {
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    padding: 10px 12px;
    background: color-mix(in srgb, var(--surface-elevated) 70%, transparent);
  }

  .dev-section[data-status='unavailable'] {
    border-style: dashed;
  }

  h3 {
    margin: 0 0 6px;
    font-size: 13px;
    letter-spacing: 0.02em;
  }

  dl {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 0;
  }

  .row {
    display: grid;
    gap: 2px;
  }

  dt {
    font-size: 11px;
    color: var(--text-muted);
  }

  dd {
    margin: 0;
    font-size: 12px;
    overflow-wrap: anywhere;
  }

  .note {
    margin: 8px 0 0;
    font-size: 11px;
    line-height: 1.45;
    color: var(--text-muted);
  }
</style>
