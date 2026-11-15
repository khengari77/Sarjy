<script lang="ts">
  import type { WordleStats } from './wordleApi';

  let { stats, unavailable }: { stats: WordleStats | null; unavailable: boolean } = $props();
</script>

<dl class="wordle-stats" aria-label="Wordle statistics">
  {#if stats}
    <div>
      <dt>Played</dt>
      <dd>{stats.played}</dd>
    </div>
    <div>
      <dt>Won</dt>
      <dd>{stats.won}</dd>
    </div>
    <div>
      <dt>Streak</dt>
      <dd>{stats.current_streak}</dd>
    </div>
    <div>
      <dt>Best</dt>
      <dd>{stats.max_streak}</dd>
    </div>
  {:else if unavailable}
    <div class="wordle-stats-missing">
      <dt>Stats</dt>
      <dd>unavailable</dd>
    </div>
  {/if}
</dl>
