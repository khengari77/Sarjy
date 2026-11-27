<script lang="ts">
  let field = $state<HTMLTextAreaElement | null>(null);
  const LINE_HEIGHT = 24;
  const MAX_HEIGHT = LINE_HEIGHT * 4;

  // Grow with the draft up to four lines, then scroll inside the field. The
  // composer pill grows upward; the controls above keep their order.
  function fit() {
    const element = field;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`;
  }

  let {
    draft = $bindable(''),
    placeholder,
    sendLabel,
    disabled,
    onsend,
    micLabel = '',
    micPressed = null,
    onmic,
  }: {
    draft?: string;
    placeholder: string;
    sendLabel: string;
    disabled: boolean;
    onsend: () => void;
    /** Accessible name for the mic button; the button is omitted without `onmic`. */
    micLabel?: string;
    /** `null` while no call is live (the button starts one); otherwise mic-on state. */
    micPressed?: boolean | null;
    onmic?: () => void;
  } = $props();

  $effect(() => {
    void draft;
    fit();
  });

  // A narrower window (or an orientation change) rewraps the same draft, so
  // the height measured for the previous width is stale. Re-fit on resize and
  // remove the listener when the field is replaced or the composer is destroyed.
  $effect(() => {
    if (!field) return;
    const refit = () => fit();
    window.addEventListener('resize', refit);
    return () => window.removeEventListener('resize', refit);
  });
</script>

<footer class="composer">
  <textarea bind:this={field} bind:value={draft} rows="1" aria-label={placeholder} {placeholder} {disabled}></textarea>
  {#if onmic}
    <button
      type="button"
      class="mic"
      {disabled}
      onclick={onmic}
      aria-label={micLabel}
      aria-pressed={micPressed === null ? undefined : micPressed}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" />
      </svg>
    </button>
  {/if}
  <button type="button" class="send" disabled={disabled || !draft.trim()} onclick={onsend} aria-label={sendLabel}>
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M3.4 20.6 21.2 12 3.4 3.4l2.1 6.6L15 12 5.5 14l-2.1 6.6Z" />
    </svg>
  </button>
</footer>

<style>
  .composer {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    /* Kit composer: a 700px, 64px-tall pill, 80px above the viewport bottom on
       a 900px-tall window and tighter as the window gets shorter. */
    max-width: 700px;
    min-height: 64px;
    margin: 8px auto clamp(12px, calc(12px + (100dvh - 640px) * 0.26), 80px);
    padding: 0 9px 0 24px;
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    background: var(--surface-elevated);
  }

  /* Up to four lines; the script sets the inline height and text past it
     scrolls inside the field. */
  .composer textarea {
    flex: 1;
    min-width: 0;
    height: 24px;
    min-height: 24px;
    max-height: 96px;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--text-primary);
    font: inherit;
    line-height: 24px;
    resize: none;
    overflow-x: hidden;
    overflow-y: auto;
    white-space: normal;
    outline: none;
  }

  .composer textarea::placeholder {
    color: var(--text-muted);
  }

  .composer button {
    display: grid;
    place-items: center;
    flex: 0 0 auto;
    width: 44px;
    height: 44px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    cursor: pointer;
    transition:
      opacity var(--transition-quick),
      background-color var(--transition-quick);
  }

  .composer .send {
    background: var(--accent);
    color: #fff;
  }

  .composer .mic {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .composer .mic[aria-pressed='false'] {
    color: var(--text-muted);
  }

  .composer button svg {
    width: 18px;
    height: 18px;
  }

  .composer .send svg {
    fill: currentColor;
  }

  .composer .mic svg {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
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

  @media (max-width: 767px) {
    .composer {
      margin-inline: 14px;
      margin-bottom: calc(16px + env(safe-area-inset-bottom));
      width: auto;
    }
  }
</style>
