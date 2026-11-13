<script lang="ts">
  import type { Turn } from '../api';

  interface Props {
    turns: Turn[];
    arabic: boolean;
    captionsVisible: boolean;
    expanded: boolean;
    provisional?: { id: string; text: string; speaker: 'user' | 'assistant' } | null;
    onexpand: (trigger?: HTMLElement) => void;
  }

  let { turns, arabic, captionsVisible, expanded, provisional = null, onexpand }: Props = $props();

  const lastUser = $derived([...turns].reverse().find((turn) => turn.role === 'user'));
  const lastAssistant = $derived([...turns].reverse().find((turn) => turn.role === 'assistant'));
  // A provisional segment takes the place of the final line for its speaker
  // so the preview never grows past two lines.
  const previewUser = $derived(
    provisional?.speaker === 'user'
      ? { text: provisional.text, provisional: true }
      : lastUser
        ? { text: line(lastUser), provisional: false }
        : null,
  );
  const previewAssistant = $derived(
    provisional?.speaker === 'assistant'
      ? { text: provisional.text, provisional: true }
      : lastAssistant
        ? { text: line(lastAssistant), provisional: false }
        : null,
  );

  function line(turn: Turn | undefined): string {
    if (!turn) return '';
    return turn.text.replace(/\s+/g, ' ').slice(0, 220);
  }
</script>

{#if captionsVisible && (turns.length || provisional)}
  <!-- Partial text is visible but not announced; the page owns a debounced
       live region for final state changes only. -->
  <div class="captions">
    {#if previewUser}
      <p class="caption user" class:provisional={previewUser.provisional} dir="auto">
        <span>{arabic ? 'أنت' : 'You'}</span>{previewUser.text}{#if previewUser.provisional}<span
            class="pending"
            aria-hidden="true">…</span
          >{/if}
      </p>
    {/if}
    {#if previewAssistant}
      <p class="caption assistant" class:provisional={previewAssistant.provisional} dir="auto">
        <span>{arabic ? 'سَرْجي' : 'Sarjy'}</span>{previewAssistant.text}{#if previewAssistant.provisional}<span
            class="pending"
            aria-hidden="true">…</span
          >{/if}
      </p>
    {/if}
    {#if !expanded}
      <button
        class="expand"
        type="button"
        aria-expanded={expanded}
        aria-controls="transcript-drawer"
        onclick={(event) => onexpand(event.currentTarget as HTMLElement)}
      >
        {arabic ? 'عرض المحادثة كاملة' : 'Show full transcript'}
      </button>
    {/if}
  </div>
{/if}

<style>
  .captions {
    width: 100%;
    max-width: 62ch;
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 0 auto;
  }

  .caption {
    margin: 0;
    display: -webkit-box;
    line-clamp: 1;
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    overflow: hidden;
    font-size: 14px;
    line-height: 1.5;
    color: var(--text-primary);
    text-align: start;
  }

  .caption span {
    color: var(--text-muted);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-inline-end: 8px;
  }

  .caption.user {
    color: var(--text-muted);
  }

  /* Provisional text stays at full opacity so it keeps WCAG AA contrast; the
     dashed rule, italics and ellipsis carry the provisional meaning instead. */
  .caption.provisional {
    font-style: italic;
    border-inline-start: 2px dashed var(--border-subtle);
    padding-inline-start: 8px;
    color: var(--text-primary);
  }

  .caption .pending {
    margin-inline-start: 4px;
    color: var(--text-muted);
  }

  .expand {
    align-self: center;
    background: transparent;
    border: 0;
    color: var(--text-muted);
    font: inherit;
    font-size: 12px;
    text-decoration: underline;
    text-underline-offset: 3px;
    cursor: pointer;
    min-height: 28px;
    padding: 2px 6px;
  }

  .expand:hover {
    color: var(--text-primary);
  }
</style>
