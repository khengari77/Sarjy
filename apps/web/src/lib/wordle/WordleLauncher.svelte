<script lang="ts">
  import WordleEntry from './WordleEntry.svelte';
  import WordleStage from './WordleStage.svelte';

  let { compact = false }: { compact?: boolean } = $props();
  let open = $state(false);
</script>

{#if compact}
  <div class="wordle-quick-entry"><WordleEntry onopen={() => (open = true)} /></div>
{:else}
  <WordleEntry onopen={() => (open = true)} />
{/if}

{#if open}<WordleStage onclose={() => (open = false)} />{/if}
