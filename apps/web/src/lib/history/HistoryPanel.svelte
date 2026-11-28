<script lang="ts">
  import ConversationList from '../chat/ConversationList.svelte';
  import type { VoiceState } from '../voice/connection';
  import type { ConversationState } from '../voice/conversations';
  import type { createSelectionController } from '../voice/selection';

  interface Props {
    conversation: ConversationState;
    voice: VoiceState;
    arabic: boolean;
    busy: boolean;
    selection: ReturnType<typeof createSelectionController>;
    onreload: () => void;
  }

  let { conversation, voice, arabic, busy, selection, onreload }: Props = $props();
</script>

<ConversationList
  conversations={conversation.conversations}
  current={conversation.current}
  {arabic}
  {busy}
  loading={conversation.loading}
  error={conversation.listError}
  retrying={conversation.listRetrying}
  callConversation={voice.live || voice.connecting ? conversation.current : null}
  onselect={selection.requestSelect}
  oncreate={selection.create}
  onretry={onreload}
  ondelete={selection.remove}
/>
