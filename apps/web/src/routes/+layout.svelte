<script lang="ts">
  import '../app.css';
  import type { Snippet } from 'svelte';
  import { page } from '$app/state';
  import EntryGate from '../lib/auth/EntryGate.svelte';

  interface Props {
    children: Snippet;
    data: { entry: boolean; error: boolean; blocked: boolean; next: string; protectedMode: boolean };
    form?: { error?: boolean; blocked?: boolean; next?: string } | null;
  }

  let { children, data, form }: Props = $props();
  // The skip link only makes sense where the studio's stage landmark exists.
  const showSkipLink = $derived(!data.entry && page.url.pathname === '/');
</script>

<svelte:head>
  <meta name="description" content="An English-first voice assistant with transparent context." />
</svelte:head>

{#if data.entry}
  <EntryGate
    error={data.error || !!form?.error}
    blocked={data.blocked || !!form?.blocked}
    next={form?.next ?? data.next}
  />
{:else}
  {#if showSkipLink}<a class="skip-link" href="#voice-stage">Skip to voice</a>{/if}
  {@render children()}
{/if}
