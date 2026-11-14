<script lang="ts">
  import type { ContextProposal } from './memory';
  import { shortId } from './memory';

  interface Props {
    diff: ContextProposal['diff'];
    title: string;
    removedLabel?: string;
    keptLabel?: string;
  }

  let { diff, title, removedLabel = 'Will be excluded', keptLabel = 'Preserved' }: Props = $props();
</script>

<section class="memory-diff" aria-label={title}>
  <h4>{title}</h4>
  <p class="memory-diff-counts">
    Revision {diff.revision_before} → {diff.revision_after}
  </p>
  <p class="memory-diff-summary">
    {diff.removed.length} excluded · {diff.kept.length} preserved
  </p>
  {#if diff.removed.length}
    <p class="memory-diff-label">{removedLabel}</p>
    <ul>
      {#each diff.removed as id (id)}
        <li><code>{shortId(id)}</code></li>
      {/each}
    </ul>
  {/if}
  {#if diff.kept.length}
    <p class="memory-diff-label">{keptLabel}</p>
    <ul>
      {#each diff.kept as id (id)}
        <li><code>{shortId(id)}</code></li>
      {/each}
    </ul>
  {/if}
  {#if diff.added.length}
    <p class="memory-diff-label">Restored</p>
    <ul>
      {#each diff.added as id (id)}
        <li><code>{shortId(id)}</code></li>
      {/each}
    </ul>
  {/if}
</section>
