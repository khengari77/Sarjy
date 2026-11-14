<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { openInvitation } from '../../lib/reviewer/invite';

  let state: 'exchanging' | 'invalid' = 'exchanging';

  onMount(async () => {
    const result = await openInvitation(location.hash, {
      fetcher: fetch,
      history,
      pathname: location.pathname + location.search,
      onBoot: () => {
        void goto(resolve('/'), { replaceState: true });
      },
    });
    state = result === 'ok' ? 'exchanging' : 'invalid';
  });
</script>

<svelte:head>
  <title>Reviewer access</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="review">
  {#if state === 'invalid'}
    <h1>Invitation unavailable</h1>
    <p>This invitation is no longer valid. Ask the team for a new link.</p>
  {:else}
    <h1>Opening your review session…</h1>
    <p>This should only take a moment.</p>
  {/if}
</main>

<style>
  .review {
    display: grid;
    place-content: center;
    min-height: 100vh;
    gap: 0.75rem;
    padding: 2rem;
    text-align: center;
  }
  h1 {
    font-size: 1.5rem;
  }
  p {
    max-width: 28rem;
    opacity: 0.8;
  }
</style>
