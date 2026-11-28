<script lang="ts">
  import type { Turn } from '../api';
  import type { ProvisionalTranscript } from '../voice/connection';
  import type { VoiceGreeting } from '../voice/greeting';

  interface Props {
    turns: Turn[];
    provisional?: ProvisionalTranscript | null;
    greeting?: VoiceGreeting | null;
    arabic: boolean;
    captionsVisible: boolean;
    /** Opens the transcript tab; receives this control so focus can return to it. */
    onopen: (opener: HTMLElement) => void;
  }

  let { turns, provisional = null, greeting = null, arabic, captionsVisible, onopen }: Props = $props();

  let button: HTMLButtonElement | undefined = $state();

  // One quoted excerpt: the assistant's latest words (a provisional segment of
  // the assistant takes the place of the final line while it is spoken). The
  // bubble caps the visible lines and scrolls; the text is never truncated.
  const lastAssistant = $derived([...turns].reverse().find((turn) => turn.role === 'assistant'));
  const quote = $derived(
    greeting
      ? { text: greeting.text, provisional: false }
      : provisional?.speaker === 'assistant'
        ? { text: provisional.text, provisional: true }
        : lastAssistant
          ? { text: lastAssistant.text.replace(/\s+/g, ' '), provisional: false }
          : null,
  );
  const label = $derived(arabic ? 'سجل المحادثة' : 'Transcript');
  // The greeting is what the caller is actually hearing, so it is shown even
  // when ordinary captions are off; it leaves when the clip ends.
  const showQuote = $derived(greeting !== null || (captionsVisible && quote !== null));
  // The button's accessible name carries only a bounded excerpt of the
  // caption, so the control's name cannot grow with a long reply.
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
     label is a bounded excerpt and the full text lives in the transcript. -->
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
