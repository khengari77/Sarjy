<script lang="ts">
  let { onopen }: { onopen: () => void } = $props();
</script>

<button class="wordle-entry" type="button" aria-haspopup="dialog" onclick={onopen}>
  <span aria-hidden="true">▦</span>
  Daily Wordle
</button>

<style>
  .wordle-entry {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 12px 12px 0;
    padding: 10px 14px;
    min-height: var(--control-height);
    font: inherit;
    font-size: 14px;
    text-align: start;
    border-radius: var(--radius-control);
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    cursor: pointer;
  }

  .wordle-entry:hover {
    background: var(--surface-hover);
  }

  @media (max-width: 767px) {
    .wordle-entry {
      margin: 10px 10px 0;
    }
  }
</style>
