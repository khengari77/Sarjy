<script lang="ts">
  import type { Conversation } from '../api';

  let {
    conversations,
    current,
    busy,
    labelNew,
    oncreate,
    onselect,
  }: {
    conversations: Conversation[];
    current: string | null;
    busy: boolean;
    labelNew: string;
    oncreate: () => void;
    onselect: (id: string) => void;
  } = $props();
</script>

<aside>
  <strong>سَرْجي <span>SARJY</span></strong>
  <button disabled={busy} onclick={oncreate}>{labelNew} +</button>
  {#each conversations as chat (chat.id)}
    <button class:chosen={current === chat.id} disabled={busy} onclick={() => onselect(chat.id)}>
      {chat.title}
    </button>
  {/each}
  <p>Voice Context Studio</p>
</aside>
