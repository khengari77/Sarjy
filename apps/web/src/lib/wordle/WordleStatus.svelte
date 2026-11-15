<script lang="ts">
  import { statusText } from './wordleState';

  let {
    view,
    notice,
    attempts,
    answer,
    remaining,
  }: {
    view: 'playing' | 'won' | 'lost';
    notice: string;
    attempts: number;
    answer?: string;
    remaining: number;
  } = $props();

  const state = $derived(view === 'playing' ? 'in_progress' : view);
</script>

{#if view === 'playing'}
  <p class="wordle-status">{notice || statusText(state, attempts, undefined, remaining)}</p>
{:else}
  <div class="wordle-result">
    <p>{statusText(state, attempts, answer)}</p>
    {#if answer}<p class="wordle-answer">The answer was <strong>{answer}</strong>.</p>{/if}
  </div>
{/if}
