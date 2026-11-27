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
    callConversation,
    onselect,
    oncreate,
    onretry,
    ondelete,
    onclose,
  }: {
    conversations: Conversation[];
    current: string | null;
    arabic: boolean;
    busy: boolean;
    loading: boolean;
    error: string;
    callConversation: string | null;
    onselect: (id: string) => void;
    oncreate: () => void;
    onretry: () => void;
    ondelete: (id: string) => Promise<string>;
    onclose?: () => void;
  } = $props();
</script>

<ChatHistorySidebar
  {conversations}
  {current}
  {arabic}
  {busy}
  {loading}
  {error}
  {callConversation}
  {onselect}
  {oncreate}
  {onretry}
  {ondelete}
  {onclose}
/>
