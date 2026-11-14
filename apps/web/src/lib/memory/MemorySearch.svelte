<script lang="ts">
  interface Props {
    query: string;
    searching: boolean;
    error: string;
    disabled: boolean;
    onsearch: (query: string) => void;
  }

  let { query = $bindable(''), searching, error, disabled, onsearch }: Props = $props();

  function submit(event: SubmitEvent) {
    event.preventDefault();
    if (!query.trim() || searching || disabled) return;
    onsearch(query.trim());
  }
</script>

<form class="memory-search" onsubmit={submit} role="search">
  <label for="memory-search-input">Search saved facts</label>
  <div class="memory-search-row">
    <input
      id="memory-search-input"
      type="search"
      bind:value={query}
      placeholder="Search what Sarjy saved"
      maxlength="4000"
      disabled={disabled || searching}
    />
    <button class="ghost-button" type="submit" disabled={disabled || searching || !query.trim()}>
      {searching ? 'Searching…' : 'Search'}
    </button>
  </div>
  {#if error}
    <p class="memory-error" role="alert">{error}</p>
  {/if}
</form>
