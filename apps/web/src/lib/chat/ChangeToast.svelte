<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { CommandSession } from '../commands/commandSession.svelte';
  import { wordsFor } from './words';

  let { session, arabic }: { session: CommandSession; arabic: boolean } = $props();

  const words = $derived(wordsFor(arabic));
  let timer: ReturnType<typeof setTimeout> | null = null;

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  // A new change restarts the quiet window; an undo in flight may finish it.
  $effect(() => {
    const id = session.change?.id ?? null;
    clearTimer();
    if (id === null) return;
    timer = setTimeout(() => {
      timer = null;
      if (!session.changeUndoing) session.dismissChange();
    }, 8000);
    return clearTimer;
  });

  onDestroy(clearTimer);
</script>

{#if session.change}
  <div class="change-toast" role="status" aria-live="polite">
    <span class="change-label">{session.change.label}</span>
    {#if session.change.undoable}
      <button
        type="button"
        class="change-undo"
        disabled={session.changeUndoing}
        onclick={() => void session.undoChange(session.change!.id)}
      >
        {words.undo}
      </button>
    {/if}
    <button type="button" class="change-dismiss" aria-label={words.dismiss} onclick={() => session.dismissChange()}>
      ×
    </button>
    {#if session.changeError}
      <p class="change-error" role="alert">{session.changeError}</p>
    {/if}
  </div>
{:else if session.changeStatus}
  <p class="change-status" role="status">{session.changeStatus}</p>
{/if}

<style>
  .change-toast,
  .change-status {
    position: fixed;
    left: 50%;
    bottom: 96px;
    z-index: 40;
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: min(520px, calc(100vw - 32px));
    padding: 10px 12px 10px 16px;
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    background: var(--surface-elevated);
    color: var(--text-primary);
    box-shadow: 0 10px 30px rgb(0 0 0 / 0.28);
    transform: translateX(-50%);
    animation: change-in 160ms ease-out;
  }

  .change-status {
    padding: 8px 14px;
    font-size: 13px;
    color: var(--text-muted);
  }

  .change-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 14px;
  }

  .change-toast button {
    flex: 0 0 auto;
    font: inherit;
    border: 0;
    border-radius: 8px;
    cursor: pointer;
    color: var(--text-primary);
    background: transparent;
  }

  .change-undo {
    padding: 4px 8px;
    color: var(--accent);
    font-weight: 600;
  }

  .change-undo:hover:enabled {
    background: var(--surface-hover);
  }

  .change-dismiss {
    width: 26px;
    height: 26px;
    font-size: 18px;
    line-height: 1;
    color: var(--text-muted);
  }

  .change-dismiss:hover {
    background: var(--surface-hover);
  }

  .change-toast button:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .change-error {
    flex-basis: 100%;
    margin: 2px 0 0;
    color: #ffb3bb;
    font-size: 12px;
  }

  @keyframes change-in {
    from {
      opacity: 0;
      transform: translate(-50%, 6px);
    }
    to {
      opacity: 1;
      transform: translate(-50%, 0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .change-toast,
    .change-status {
      animation: none;
    }
  }
</style>
