<script lang="ts">
  /**
   * The overlay drawer's close control. It is rendered first in each drawer so
   * it becomes the first focusable element; docked panels hide it because the
   * header toggle already owns closing.
   */
  let { arabic = false, onclose }: { arabic?: boolean; onclose: () => void } = $props();
</script>

<button type="button" class="drawer-close" aria-label={arabic ? 'إغلاق' : 'Close'} onclick={onclose}>
  <span aria-hidden="true">✕</span>
</button>

<style>
  .drawer-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    align-self: flex-end;
    flex: 0 0 auto;
    width: 44px;
    height: 44px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--text-muted);
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
  }

  .drawer-close:hover,
  .drawer-close:focus-visible {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  @media (min-width: 1200px) {
    .drawer-close {
      display: none;
    }
  }
</style>
