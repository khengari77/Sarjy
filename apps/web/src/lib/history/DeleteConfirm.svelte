<script lang="ts">
  import { tick } from 'svelte';
  import type { Words } from '../chat/words';

  let {
    words,
    busy,
    deleting,
    error,
    oncancel,
    onconfirm,
  }: {
    words: Words;
    busy: boolean;
    deleting: boolean;
    error: string;
    oncancel: () => void;
    onconfirm: () => void;
  } = $props();

  let group = $state<HTMLElement | null>(null);
  let cancelButton = $state<HTMLButtonElement | null>(null);

  // Disabling the focused Delete button drops focus to <body>; keep keyboard
  // ownership inside the confirmation while the request is in flight.
  $effect(() => {
    if (!deleting) return;
    void tick().then(() => (cancelButton ?? group)?.focus({ preventScroll: true }));
  });
</script>

<div class="menu-confirm" role="group" aria-label={words.deleteConfirm} tabindex="-1" bind:this={group}>
  <p class="menu-confirm-title">{words.deleteConfirm}</p>
  <p>{words.deleteWarning}</p>
  <div class="menu-actions">
    <button
      type="button"
      bind:this={cancelButton}
      aria-disabled={deleting ? 'true' : undefined}
      onclick={() => {
        if (!deleting) oncancel();
      }}
    >
      {words.cancel}
    </button>
    <button
      type="button"
      class="danger"
      disabled={busy || deleting}
      aria-label={words.deleteAction}
      onclick={onconfirm}
    >
      {deleting ? words.deleting : words.delete}
    </button>
  </div>
  {#if error}
    <p class="menu-error" role="alert">{error}</p>
  {/if}
</div>
