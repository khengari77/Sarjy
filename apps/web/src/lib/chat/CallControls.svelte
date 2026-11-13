<script lang="ts">
  import type { SessionConfig } from '../api';
  import type { Words } from './words';

  let {
    live,
    busy,
    muted,
    canConnect,
    words,
    onend,
    ontoggle,
    oninterrupt,
    onconnect,
  }: {
    live: SessionConfig | null;
    busy: boolean;
    muted: boolean;
    canConnect: boolean;
    words: Words;
    onend: () => void;
    ontoggle: () => void;
    oninterrupt: () => void;
    onconnect: () => void;
  } = $props();
</script>

<nav>
  {#if live}
    <button disabled={busy} onclick={onend}>{words.end}</button>
    <button onclick={ontoggle}>{muted ? words.unmute : words.mute}</button>
    <button onclick={oninterrupt}>Stop / إيقاف</button>
  {:else}
    <button disabled={busy || !canConnect} onclick={onconnect}>{words.call}</button>
  {/if}
</nav>
