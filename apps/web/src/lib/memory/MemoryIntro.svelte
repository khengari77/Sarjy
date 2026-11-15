<script lang="ts">
  interface Props {
    conversationId: string | null;
  }

  let { conversationId }: Props = $props();
</script>

<header class="memory-header">
  <h3>Memory &amp; context</h3>
  <p class="memory-muted">Every saved fact is visible here with its source; states stay separate.</p>
</header>

{#if !conversationId}
  <p class="memory-notice" role="status">
    Select a conversation to see whether a fact is a candidate for its next compiled context or excluded from it. Stored
    facts are listed below.
  </p>
{/if}

<p class="memory-muted">
  “Candidate” means the fact is inside the set the compiler builds the next prompt from. The final prompt can still trim
  it to fit its character budget, so this is candidacy, not a promise the model received it.
</p>

<p class="memory-footnote">
  Hiding a memory is reversible and applies everywhere: the row stays in the list, marked Hidden, until you restore it. Deleting is permanent
  and only possible here, never by voice.
</p>
