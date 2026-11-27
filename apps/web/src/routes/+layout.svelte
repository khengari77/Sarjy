<script lang="ts">
  import '../app.css';
  import type { Snippet } from 'svelte';
  import EntryGate from '../lib/auth/EntryGate.svelte';

  interface Props {
    children: Snippet;
    data: { entry: boolean; error: boolean; blocked: boolean; next: string; protectedMode: boolean };
    form?: { error?: boolean; blocked?: boolean; next?: string } | null;
  }

  let { children, data, form }: Props = $props();
</script>

<svelte:head>
  <title>Sarjy — Voice Context Studio</title>
  <meta name="description" content="An English-first voice assistant with transparent context." />
</svelte:head>

{#if data.entry}
  <EntryGate
    error={data.error || !!form?.error}
    blocked={data.blocked || !!form?.blocked}
    next={form?.next ?? data.next}
  />
{:else}
  <a class="skip-link" href="#voice-stage">Skip to voice</a>
  {@render children()}
{/if}
