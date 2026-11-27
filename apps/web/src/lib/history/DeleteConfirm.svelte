<script lang="ts">
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
</script>

<div class="menu-confirm" role="group" aria-label={words.deleteConfirm}>
  <p class="menu-confirm-title">{words.deleteConfirm}</p>
  <p>{words.deleteWarning}</p>
  <div class="menu-actions">
    <button type="button" disabled={deleting} onclick={oncancel}>
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
