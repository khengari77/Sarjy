<script lang="ts">
  let {
    draft = $bindable(''),
    placeholder,
    sendLabel,
    disabled,
    onsend,
  }: {
    draft?: string;
    placeholder: string;
    sendLabel: string;
    disabled: boolean;
    onsend: () => void;
  } = $props();
</script>

<footer>
  <textarea bind:value={draft} aria-label={placeholder} {placeholder} {disabled}></textarea>
  <button disabled={disabled || !draft.trim()} onclick={onsend}>{sendLabel} ↗</button>
</footer>
