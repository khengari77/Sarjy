<script lang="ts">
  import { tick } from 'svelte';
  import { wrapTab } from '../ui/drawerFocus';

  interface Props {
    content: string;
    busy: boolean;
    /** Element that opened the dialog; focus returns here (or to `fallback`). */
    trigger: HTMLElement | null;
    fallback: HTMLElement | null;
    ondelete: () => void;
    oncancel: () => void;
  }

  let { content, busy, trigger, fallback, ondelete, oncancel }: Props = $props();

  let dialog = $state<HTMLElement | null>(null);
  let cancelButton = $state<HTMLButtonElement | null>(null);
  const preview = $derived(content.length > 140 ? `${content.slice(0, 140)}…` : content);
  const dir = $derived((trigger?.closest('[dir]')?.getAttribute('dir') ?? undefined) as 'ltr' | 'rtl' | undefined);

  // Lives on <body>: the drawer is transformed off-canvas, which would break
  // `position: fixed`, and its Escape handling must not also close the drawer.
  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    return { destroy: () => node.remove() };
  }

  $effect(() => {
    void tick().then(() => cancelButton?.focus());
    return () => {
      // The row may be gone after a delete; fall back to a stable element.
      const target = trigger?.isConnected ? trigger : fallback;
      void tick().then(() => target?.focus({ preventScroll: true }));
    };
  });

  function onkeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      if (!busy) oncancel();
    } else if (event.key === 'Tab' && dialog) {
      event.stopPropagation();
      wrapTab(event, [dialog]);
    }
  }
</script>

<div class="memory-scrim" role="presentation" use:portal {dir}>
  <div
    class="memory-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="memory-delete-title"
    aria-describedby="memory-delete-body"
    tabindex="-1"
    bind:this={dialog}
    {onkeydown}
  >
    <h2 id="memory-delete-title">Delete this memory?</h2>
    <p class="memory-dialog-quote">“{preview}”</p>
    <p id="memory-delete-body">
      This permanently deletes the memory and cannot be undone. It can only be done here, never by voice.
    </p>
    <div class="memory-dialog-actions">
      <button type="button" class="ghost-button" bind:this={cancelButton} disabled={busy} onclick={oncancel}>
        Cancel
      </button>
      <button type="button" class="danger-button" disabled={busy} onclick={ondelete}>
        {busy ? 'Deleting…' : 'Delete'}
      </button>
    </div>
  </div>
</div>
