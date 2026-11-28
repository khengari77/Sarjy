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
    retrying,
    callConversation,
    onselect,
    oncreate,
    onretry,
    ondelete,
  }: {
    conversations: Conversation[];
    current: string | null;
    arabic: boolean;
    busy: boolean;
    loading: boolean;
    error: string;
    retrying: boolean;
    callConversation: string | null;
    onselect: (id: string) => void;
    oncreate: () => void;
    onretry: () => void;
    ondelete: (id: string) => Promise<string>;
  } = $props();
</script>

<ChatHistorySidebar
  {conversations}
  {current}
  {arabic}
  {busy}
  {loading}
  {error}
  {retrying}
  {callConversation}
  {onselect}
  {oncreate}
  {onretry}
  {ondelete}
/>
