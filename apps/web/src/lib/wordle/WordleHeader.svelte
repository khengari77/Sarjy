<script lang="ts">
  let {
    dateLabel,
    puzzleDate,
    countdown,
    timezone,
    onclose,
    onreset,
  }: {
    dateLabel: string;
    puzzleDate: string;
    countdown: string;
    timezone: string;
    onclose: () => void;
    onreset: () => void;
  } = $props();
</script>

<header class="wordle-head">
  <div>
    <h2 id="wordle-title">Daily Wordle</h2>
    {#if dateLabel}
      <p class="wordle-date"><time datetime={puzzleDate}>{dateLabel}</time></p>
    {/if}
  </div>
  <div class="wordle-head-actions">
    <button class="wordle-close wordle-reset" type="button" onclick={onreset} aria-label="Reset today's game">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
        <path
          d="M4 12a8 8 0 1 0 2.5-5.8M4 4v4.5h4.5"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>
    <button class="wordle-close" type="button" onclick={onclose} aria-label="Close Wordle">✕</button>
  </div>
</header>

{#if puzzleDate}
  <p class="wordle-countdown">
    Next puzzle in <time>{countdown}</time>
    <span class="wordle-tz">({timezone.replace('_', ' ')})</span>
  </p>
{/if}
