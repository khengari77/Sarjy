<script lang="ts">
  type Kind = 'hide' | 'restore' | 'delete';

  interface Props {
    kind: Kind;
    disabled?: boolean;
    onclick: (event: MouseEvent) => void;
  }

  let { kind, disabled = false, onclick }: Props = $props();

  // Icon-only: the accessible name and the tooltip carry the meaning.
  const LABELS: Record<Kind, string> = { hide: 'Hide memory', restore: 'Restore memory', delete: 'Delete memory' };
</script>

<button
  type="button"
  class="memory-icon-button"
  data-kind={kind}
  aria-label={LABELS[kind]}
  title={LABELS[kind]}
  {disabled}
  {onclick}
>
  <svg
    viewBox="0 0 24 24"
    width="20"
    height="20"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    {#if kind === 'hide'}
      <path d="M3 3l18 18" />
      <path
        d="M10.6 5.1A9.7 9.7 0 0 1 12 5c5 0 8.5 4.2 9.5 7a11.6 11.6 0 0 1-2.6 3.8M6.5 6.6A11.5 11.5 0 0 0 2.5 12c1 2.8 4.5 7 9.5 7 1.5 0 2.8-.4 4-1"
      />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    {:else if kind === 'restore'}
      <path d="M2.5 12C3.5 9.2 7 5 12 5s8.5 4.2 9.5 7c-1 2.8-4.5 7-9.5 7s-8.5-4.2-9.5-7z" />
      <circle cx="12" cy="12" r="3" />
    {:else}
      <path
        d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 1.8h6a2 2 0 0 0 2-1.8l1-12M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7"
      />
    {/if}
  </svg>
</button>
