<script lang="ts">
  let {
    dateLabel,
    puzzleDate,
    countdown,
    timezone,
    onclose,
  }: {
    dateLabel: string;
    puzzleDate: string;
    countdown: string;
    timezone: string;
    onclose: () => void;
  } = $props();
</script>

<header class="wordle-head">
  <div>
    <h2 id="wordle-title">Daily Wordle</h2>
    {#if dateLabel}
      <p class="wordle-date"><time datetime={puzzleDate}>{dateLabel}</time></p>
    {/if}
  </div>
  <button class="wordle-close" type="button" onclick={onclose} aria-label="Close Wordle">✕</button>
</header>

{#if puzzleDate}
  <p class="wordle-countdown">
    Next puzzle in <time>{countdown}</time>
    <span class="wordle-tz">({timezone.replace('_', ' ')})</span>
  </p>
{/if}
