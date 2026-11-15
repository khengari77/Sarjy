<script lang="ts">
  import { onMount } from 'svelte';

  let { busy, onconfirm, oncancel }: { busy: boolean; onconfirm: () => void; oncancel: () => void } = $props();
  let cancelButton = $state<HTMLButtonElement>();

  onMount(() => cancelButton?.focus());

  function handleKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return;
    // Escape closes only this question; the stage keeps its own Escape for the game.
    event.preventDefault();
    oncancel();
  }
</script>

<div
  class="wordle-confirm"
  role="alertdialog"
  aria-labelledby="wordle-reset-title"
  aria-describedby="wordle-reset-body"
  tabindex="-1"
  onkeydown={handleKeydown}
>
  <h3 id="wordle-reset-title">Reset today's game?</h3>
  <p id="wordle-reset-body">
    This deletes your guesses for today's puzzle and removes the game from your stats. You can then replay the same
    puzzle from scratch.
  </p>
  <div class="wordle-confirm-actions">
    <button class="wordle-action" type="button" bind:this={cancelButton} onclick={oncancel}>Keep playing</button>
    <button class="wordle-action wordle-danger" type="button" disabled={busy} onclick={onconfirm}>Reset game</button>
  </div>
</div>
