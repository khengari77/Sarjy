<script lang="ts">
  import '../app.css';
  import type { Snippet } from 'svelte';
  import EntryGate from '../lib/auth/EntryGate.svelte';
  import SignOut from '../lib/auth/SignOut.svelte';

  interface Props {
    children: Snippet;
    data: { entry: boolean; error: boolean; next: string; protectedMode: boolean };
    form?: { error?: boolean; next?: string } | null;
  }

  let { children, data, form }: Props = $props();
</script>

{#if data.entry}
  <EntryGate error={data.error || !!form?.error} next={form?.next ?? data.next} />
{:else}
  {@render children()}
  {#if data.protectedMode}<SignOut />{/if}
{/if}
