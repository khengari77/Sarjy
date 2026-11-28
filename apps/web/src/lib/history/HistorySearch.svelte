<script lang="ts">
  let { arabic, query = $bindable('') }: { arabic: boolean; query?: string } = $props();
  const label = $derived(arabic ? 'البحث في المحادثات' : 'Search conversations');
</script>

<label class="search">
  <span class="sr-only">{label}</span>
  <svg class="search-icon" aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none">
    <circle cx="10.8" cy="10.8" r="6.3" stroke="currentColor" stroke-width="1.7" />
    <path d="m15.6 15.6 4.1 4.1" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
  </svg>
  <input type="search" bind:value={query} placeholder={label} aria-label={label} />
  {#if query}
    <button type="button" class="clear" onclick={() => (query = '')} aria-label={arabic ? 'مسح البحث' : 'Clear search'}>
      ✕
    </button>
  {/if}
</label>
