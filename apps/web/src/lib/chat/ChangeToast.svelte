<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { CommandSession } from '../commands/commandSession.svelte';
  import { createChangeToastTiming } from './changeToastTiming';
  import { wordsFor } from './words';

  let { session, arabic }: { session: CommandSession; arabic: boolean } = $props();

  const words = $derived(wordsFor(arabic));
  const timing = createChangeToastTiming({
    isUndoing: () => session.changeUndoing,
    hasError: () => session.changeError.length > 0,
    dismiss: () => session.dismissChange(),
    clearStatus: () => session.clearChangeStatus(),
  });

  // A new change, or an undo attempt settling, restarts the quiet window.
  $effect(() => {
    void session.change?.id;
    void session.changeUndoing;
    void session.changeError;
    timing.syncChange(session.change?.id ?? null);
  });

  // "Undone." is a short confirmation, never a permanent overlay.
  $effect(() => {
    timing.syncStatus(session.changeStatus);
  });

  onDestroy(() => timing.destroy());
</script>

{#if session.change || session.changeStatus}
  <!-- The visual toast may be hidden when no room is left above the dock;
       this live region keeps the message in the accessibility tree. -->
  <p class="sr-only change-live" role="status" aria-live="polite">
    {session.change?.label ?? session.changeStatus}
  </p>
  <!-- Mounted outside AppShell's `.shell`, so the layer carries the UI
       direction itself instead of inheriting the document's LTR. Requires
       AppShell to be mounted with its controls snippet: the band is measured
       from that dock. -->
  <div class="change-toast-layer" dir={arabic ? 'rtl' : 'ltr'}>
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
  </div>
{/if}

<style>
  /* A fixed band between the topbar and the dock. Anchoring the toast to the
     band's bottom keeps it above the controls and caps its height: when the
     dock scrolls below a short viewport the toast sits at the viewport bottom
     instead of leaving the screen. */
  .change-toast-layer {
    position: fixed;
    top: var(--toast-top, 76px);
    bottom: var(--toast-bottom, 108px);
    left: var(--main-column-center, 50%);
    /* Above the controls strip (26), below the drawers (30); the scrim (25)
       is below both. An open modal drawer must win the hit test over the toast. */
    z-index: 29;
    display: flex;
    align-items: flex-end;
    justify-content: center;
    width: min(520px, calc(100vw - 32px));
    transform: translateX(-50%);
    pointer-events: none;
    visibility: hidden;
  }

  :global(html[data-toast-ready]) .change-toast-layer {
    visibility: visible;
  }

  /* When the visual toast is visible it already announces; hide the fallback
     live region so the change is not read twice. */
  :global(html[data-toast-ready]) .change-live {
    display: none;
  }

  .change-toast,
  .change-status {
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: 100%;
    max-height: 100%;
    padding: 10px 12px 10px 16px;
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    background: var(--surface-elevated);
    color: var(--text-primary);
    box-shadow: 0 10px 30px rgb(0 0 0 / 0.28);
    overflow-y: auto;
    pointer-events: auto;
    animation: change-in 160ms ease-out;
  }

  .change-status {
    padding: 8px 14px;
    font-size: 13px;
    color: var(--text-muted);
    pointer-events: none;
  }

  /* Let the full-width Undo error line wrap onto its own row. */
  .change-toast {
    flex-wrap: wrap;
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

  /* Higher specificity than `.change-toast button`, so Undo keeps the accent
     colour and a full tap target. */
  .change-toast .change-undo {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 4px 8px;
    color: var(--accent-text);
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

  /* Touch pointers get the same 44px target as Undo. */
  @media (pointer: coarse) {
    .change-dismiss {
      width: 44px;
      height: 44px;
    }
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
    color: var(--status-error-text);
    font-size: 12px;
  }

  @keyframes change-in {
    from {
      opacity: 0;
      transform: translateY(6px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .change-toast,
    .change-status {
      animation: none;
    }
  }
</style>
