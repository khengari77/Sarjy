<script lang="ts">
  let { view, notice, onretry }: { view: 'loading' | 'unavailable' | 'error'; notice: string; onretry: () => void } =
    $props();
</script>

{#if view === 'loading'}
  <div class="wordle-panel" aria-busy="true" aria-live="polite">
    <p>Loading today's puzzle…</p>
  </div>
{:else}
  <div class="wordle-panel" role="alert" data-code={view === 'unavailable' ? 'WORDLE_UNAVAILABLE' : 'WORDLE_ERROR'}>
    <p class="wordle-notice">{notice}</p>
    <p class="wordle-hint">No board is shown because the puzzle could not be loaded from the server.</p>
    <button class="wordle-action" type="button" onclick={onretry}>Retry</button>
  </div>
{/if}
