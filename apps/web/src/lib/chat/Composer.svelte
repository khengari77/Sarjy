<script lang="ts">
  let {
    draft = $bindable(''),
    placeholder,
    sendLabel,
    disabled,
    onsend,
    onmic,
  }: {
    draft?: string;
    placeholder: string;
    sendLabel: string;
    disabled: boolean;
    onsend: () => void;
    /**
     * Optional voice entry point. The host wires this to the same connection
     * path the orb uses; when it is omitted the mic button is not rendered at
     * all, so the composer never implies a voice state it does not have.
     */
    onmic?: () => void;
  } = $props();
</script>

<footer class="composer">
  <textarea rows="1" bind:value={draft} aria-label={placeholder} {placeholder} {disabled}></textarea>

  {#if onmic}
    <button type="button" class="mic" aria-label="Start voice conversation" {disabled} onclick={onmic}>
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M12 15a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v7a3 3 0 0 0 3 3Zm-7-4v1a7 7 0 0 0 14 0v-1M12 19v4M8 23h8"
          fill="none"
          stroke="currentColor"
          stroke-width="1.7"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>
  {/if}

  <button
    type="button"
    class="send"
    disabled={disabled || !draft.trim()}
    aria-label={sendLabel}
    title={sendLabel}
    onclick={onsend}
  >
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="m5 12 14-7-4 14-3-6-7-1Zm7 1 7-8"
        fill="none"
        stroke="currentColor"
        stroke-width="1.7"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  </button>
</footer>

<style>
  /*
   * Living Cloud v2.1 composer (design kit `Composer` component).
   * `chat.css` owns the shared chat surface, so every kit-specific rule stays
   * scoped here; `.composer` is still on the root so the shared row layout
   * (max-width, centring) applies as a base we refine.
   */
  .composer {
    display: flex;
    align-items: center;
    gap: 8px;
    width: calc(100% - 32px);
    max-width: var(--stage-max-width);
    margin: 0 auto calc(6px + env(safe-area-inset-bottom));
    padding: 8px;
    min-height: 58px;
    box-sizing: border-box;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-composer);
    transition:
      border-color var(--transition-quick),
      box-shadow var(--transition-quick);
  }

  /* The field itself is chrome-free; the pill shows the focus state instead. */
  .composer:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--focus-ring) 45%, transparent);
  }

  .composer textarea {
    flex: 1 1 auto;
    min-width: 0;
    min-height: 42px;
    max-height: 140px;
    margin: 0;
    padding: 9px 6px 9px 12px;
    border: 0;
    border-radius: calc(var(--radius-composer) - 10px);
    background: transparent;
    color: var(--text-primary);
    font: inherit;
    font-size: 15px;
    line-height: 1.4;
    resize: none;
  }

  .composer textarea::placeholder {
    color: var(--text-muted);
  }

  .composer textarea:focus,
  .composer textarea:focus-visible {
    outline: none;
  }

  .mic,
  .send {
    flex: 0 0 auto;
    display: inline-grid;
    place-items: center;
    width: 44px;
    height: 44px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    color: #fff;
    cursor: pointer;
    transition:
      background var(--transition-quick),
      opacity var(--transition-quick);
  }

  .mic {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .mic:hover:enabled {
    background: color-mix(in srgb, var(--text-primary) 16%, var(--surface-hover));
  }

  .send {
    background: var(--accent);
  }

  .send:hover:enabled {
    background: color-mix(in srgb, var(--accent) 86%, black);
  }

  .mic:disabled,
  .send:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .mic:focus-visible,
  .send:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }

  .mic svg,
  .send svg {
    width: 20px;
    height: 20px;
  }

  @media (max-width: 767px) {
    .composer {
      width: calc(100% - 24px);
    }
  }
</style>
