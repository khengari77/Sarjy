<script lang="ts">
  import type { Turn } from '../api';

  interface Props {
    turns: Turn[];
    arabic: boolean;
    captionsVisible: boolean;
    expanded: boolean;
    onexpand: () => void;
  }

  let { turns, arabic, captionsVisible, expanded, onexpand }: Props = $props();

  const lastUser = $derived([...turns].reverse().find((turn) => turn.role === 'user'));
  const lastAssistant = $derived([...turns].reverse().find((turn) => turn.role === 'assistant'));

  function line(turn: Turn | undefined): string {
    if (!turn) return '';
    return turn.text.replace(/\s+/g, ' ').slice(0, 220);
  }
</script>

{#if captionsVisible && turns.length}
  <div class="captions" aria-live="polite" aria-atomic="true">
    {#if lastUser}
      <p class="caption user" dir="auto"><span>{arabic ? 'أنت' : 'You'}</span>{line(lastUser)}</p>
    {/if}
    {#if lastAssistant}
      <p class="caption assistant" dir="auto"><span>{arabic ? 'سَرْجي' : 'Sarjy'}</span>{line(lastAssistant)}</p>
    {/if}
    {#if !expanded}
      <button class="expand" type="button" onclick={onexpand}>
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
