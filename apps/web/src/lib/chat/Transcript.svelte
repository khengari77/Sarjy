<script lang="ts">
  import type { Turn } from '../api';

  let {
    turns,
    toolCalls,
    title,
    intro,
  }: {
    turns: Turn[];
    toolCalls: { id: string; name: string; result: unknown }[];
    title: string;
    intro: string;
  } = $props();
</script>

<section>
  {#if turns.length}
    {#each turns as turn (turn.id)}
      <article dir="auto">
        <small>{turn.role} · {turn.status}</small>
        <p>{turn.text}</p>
      </article>
    {/each}
  {:else}
    <div class="orb">س</div>
    <h1>{title}</h1>
    <p>{intro}</p>
  {/if}
  {#each toolCalls as tool (tool.id)}
    <details>
      <summary>{tool.name} · Open-Meteo</summary>
      <pre dir="ltr">{JSON.stringify(tool.result, null, 2)}</pre>
    </details>
  {/each}
</section>
