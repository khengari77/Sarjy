<script lang="ts">
  import type { Conversation } from '../api';
  import ChatHistorySidebar from '../history/ChatHistorySidebar.svelte';

  let {
    conversations,
    current,
    arabic,
    busy,
    loading,
    error,
    onselect,
    oncreate,
    onretry,
  }: {
    conversations: Conversation[];
    current: string | null;
    arabic: boolean;
    busy: boolean;
    loading: boolean;
    error: string;
    onselect: (id: string) => void;
    oncreate: () => void;
    onretry: () => void;
  } = $props();
</script>

<ChatHistorySidebar {conversations} {current} {arabic} {busy} {loading} {error} {onselect} {oncreate} {onretry} />
