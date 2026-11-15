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

  // One quoted line: the assistant's latest words (a provisional segment of
  // the assistant takes the place of the final line while it is spoken).
  const lastAssistant = $derived([...turns].reverse().find((turn) => turn.role === 'assistant'));
  const quote = $derived(
    provisional?.speaker === 'assistant'
      ? { text: provisional.text, provisional: true }
      : lastAssistant
        ? { text: lastAssistant.text.replace(/\s+/g, ' ').slice(0, 220), provisional: false }
        : null,
  );
  const label = $derived(arabic ? 'سجل المحادثة' : 'Transcript');
  const showQuote = $derived(captionsVisible && quote !== null);
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
  aria-label={showQuote ? `${label}: ${quote?.text}` : label}
  aria-haspopup="dialog"
  onclick={() => button && onopen(button)}
>
  {#if showQuote && quote}
    <span class="caption-text" dir="auto">“{quote.text}{quote.provisional ? '…' : ''}”</span>
  {:else}
    <span class="caption-text">{label}</span>
  {/if}
</button>
