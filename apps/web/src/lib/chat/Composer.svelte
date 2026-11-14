<script lang="ts">
  let {
    draft = $bindable(''),
    placeholder,
    sendLabel,
    disabled,
    onsend,
  }: {
    draft?: string;
    placeholder: string;
    sendLabel: string;
    disabled: boolean;
    onsend: () => void;
  } = $props();
</script>

<footer class="composer">
  <textarea bind:value={draft} aria-label={placeholder} {placeholder} {disabled}></textarea>
  <button disabled={disabled || !draft.trim()} onclick={onsend}>{sendLabel} ↗</button>
</footer>

<style>
  .composer {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    max-width: var(--stage-max-width);
    margin: 0 auto;
    padding: 7px 7px 7px 18px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-composer);
    background: var(--surface-elevated);
  }

  .composer textarea {
    flex: 1;
    min-height: 24px;
    max-height: 140px;
    padding: 10px 0;
    border: 0;
    background: transparent;
    color: var(--text-primary);
    font: inherit;
    line-height: 1.4;
    resize: none;
    outline: none;
  }

  .composer textarea::placeholder {
    color: var(--text-muted);
  }

  .composer button {
    display: grid;
    place-items: center;
    flex: 0 0 auto;
    width: 42px;
    height: 42px;
    border: 0;
    border-radius: 999px;
    background: var(--accent);
    color: #fff;
    font: inherit;
    font-size: 0;
    cursor: pointer;
    transition: opacity var(--transition-quick);
  }

  .composer button::after {
    content: '↗';
    font-size: 18px;
    line-height: 1;
  }

  .composer button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .composer button:focus-visible,
  .composer textarea:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }
</style>
