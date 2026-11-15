<script lang="ts">
  import type { Turn } from '../api';
  import type { ProvisionalTranscript } from '../voice/connection';
  import ToolTraceChip from './ToolTraceChip.svelte';

  interface Props {
    turns: Turn[];
    tools: { id: string; name: string; result: unknown }[];
    arabic: boolean;
    provisional?: ProvisionalTranscript | null;
  }

  let { turns, tools, arabic, provisional = null }: Props = $props();

  function speaker(role: string): string {
    if (role === 'assistant') return arabic ? 'سَرْجي' : 'Sarjy';
    return arabic ? 'أنت' : 'You';
  }

  /** Only a turn that did not complete normally earns a status. */
  function status(turn: Turn): string | null {
    if (turn.status === 'interrupted') return arabic ? 'مقاطَع' : 'Interrupted';
    if (turn.status === 'failed') return arabic ? 'فشل' : 'Failed';
    return null;
  }
</script>

<section class="transcript" aria-label={arabic ? 'سجل المحادثة' : 'Conversation transcript'}>
  {#if turns.length === 0 && !provisional}
    <p class="empty">{arabic ? 'لا توجد رسائل بعد.' : 'No messages yet.'}</p>
  {/if}
  {#each turns as turn (turn.id)}
    {@const note = status(turn)}
    <article class="turn" class:assistant={turn.role === 'assistant'} dir="auto">
      <small>
        {speaker(turn.role)}{#if note}<span class="note" data-status={turn.status}>{` · ${note}`}</span>{/if}
      </small>
      <p>{turn.text}</p>
    </article>
  {/each}
  {#if provisional}
    <article class="turn provisional" dir="auto" aria-label={arabic ? 'نص مؤقت' : 'Provisional transcript'}>
      <small>{speaker(provisional.speaker)}</small>
      <p>{provisional.text}…</p>
    </article>
  {/if}
  {#if tools.length}
    <div class="traces">
      {#each tools as tool (tool.id)}
        <ToolTraceChip name={tool.name} result={tool.result} {arabic} />
      {/each}
    </div>
  {/if}
</section>

<style>
  .transcript {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .turn {
    text-align: start;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-panel);
    padding: 10px 14px;
  }

  .turn.assistant {
    background: color-mix(in srgb, var(--surface-elevated) 70%, var(--bg-app));
  }

  .turn small {
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }

  .turn .note {
    font-weight: 400;
    opacity: 0.8;
  }

  .turn.provisional {
    border-style: dashed;
    opacity: 0.75;
    font-style: italic;
  }

  .turn p {
    margin: 4px 0 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 14px;
    line-height: 1.45;
  }

  .traces {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .empty {
    margin: 0;
    color: var(--text-muted);
    font-size: 14px;
  }
</style>
