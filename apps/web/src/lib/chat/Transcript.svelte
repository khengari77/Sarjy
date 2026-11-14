<script lang="ts">
  import type { Turn } from '../api';
  import LiveCaptionPreview from './LiveCaptionPreview.svelte';
  import type { ProvisionalTranscript } from '../voice/connection';

  let {
    turns,
    provisional = null,
    arabic,
    captionsVisible,
    expanded,
    onexpand,
  }: {
    turns: Turn[];
    provisional?: ProvisionalTranscript | null;
    tools: { id: string; name: string; result: unknown }[];
    arabic: boolean;
    captionsVisible: boolean;
    expanded: boolean;
    onexpand: () => void;
  } = $props();
</script>

{#if turns.length || provisional}
  <LiveCaptionPreview {turns} {provisional} {arabic} {captionsVisible} {expanded} {onexpand} />
{/if}
