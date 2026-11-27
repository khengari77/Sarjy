<script lang="ts">
  import type { Turn } from '../api';
  import type { ProvisionalTranscript } from '../voice/connection';

  interface Props {
    turns: Turn[];
    provisional?: ProvisionalTranscript | null;
    arabic: boolean;
    captionsVisible: boolean;
    /** Opens the transcript tab; receives this control so focus can return to it. */
    onopen: (opener: HTMLElement) => void;
  }

  let { turns, provisional = null, arabic, captionsVisible, onopen }: Props = $props();

  let button: HTMLButtonElement | undefined = $state();

  // One quoted excerpt: the assistant's latest words (a provisional segment of
  // the assistant takes the place of the final line while it is spoken). The
  // bubble caps the visible lines and scrolls; the text is never truncated.
  const lastAssistant = $derived([...turns].reverse().find((turn) => turn.role === 'assistant'));
  const quote = $derived(
    provisional?.speaker === 'assistant'
      ? { text: provisional.text, provisional: true }
      : lastAssistant
        ? { text: lastAssistant.text.replace(/\s+/g, ' '), provisional: false }
        : null,
  );
  const label = $derived(arabic ? 'سجل المحادثة' : 'Transcript');
  const showQuote = $derived(captionsVisible && quote !== null);
  // The button's accessible name carries only a bounded excerpt. The live
  // region already announces final turns, so repeating the whole caption here
  // would read it twice and make the control's name unbounded.
  const EXCERPT_LENGTH = 200;
  const ariaLabel = $derived.by(() => {
    if (!showQuote || !quote) return label;
    const excerpt =
      quote.text.length > EXCERPT_LENGTH ? `${quote.text.slice(0, EXCERPT_LENGTH).trimEnd()}…` : quote.text;
    return `${label}: ${excerpt}`;
  });
</script>

<!-- The bubble is the keyboard-accessible control that opens the right drawer
     on its Transcript tab. Partial text is visible but never announced; the
     page owns the debounced live region for final state changes. -->
<button
  class="caption-bubble"
  class:provisional={showQuote && quote?.provisional}
  class:quiet={!showQuote}
  type="button"
  bind:this={button}
  aria-label={ariaLabel}
  aria-haspopup="dialog"
  onclick={() => button && onopen(button)}
>
  {#if showQuote && quote}
    <span class="caption-text" dir="auto">“{quote.text}{quote.provisional ? '…' : ''}”</span>
  {:else}
    <span class="caption-text">{label}</span>
  {/if}
</button>
