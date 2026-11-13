<script lang="ts">
  import type { SessionConfig } from '../api';
  import type { Words } from './words';

  let {
    live,
    connecting,
    busy,
    muted,
    connectionLost,
    canInterrupt,
    words,
    onend,
    oncancel,
    ontoggle,
    oninterrupt,
    onconnect,
  }: {
    live: SessionConfig | null;
    connecting: boolean;
    busy: boolean;
    muted: boolean;
    connectionLost: boolean;
    canInterrupt: boolean;
    words: Words;
    onend: () => void;
    oncancel: () => void;
    ontoggle: () => void;
    oninterrupt: () => void;
    onconnect: () => void;
  } = $props();
</script>

<div class="controls">
  {#if connecting}
    <button class="ghost-button" onclick={oncancel}>Cancel connecting</button>
  {:else if live}
    <button class="primary-button" disabled={busy} onclick={onend}>{words.end}</button>
    <button class="ghost-button" disabled={busy || connectionLost} onclick={ontoggle}>
      {muted ? words.unmute : words.mute}
    </button>
    <button class="ghost-button" disabled={busy || !canInterrupt} onclick={oninterrupt}> Stop </button>
  {:else}
    <button class="primary-button" disabled={busy} onclick={onconnect}>{words.call}</button>
  {/if}
</div>
