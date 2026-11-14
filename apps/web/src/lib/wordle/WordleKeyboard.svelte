<script lang="ts">
  import type { Mark } from './wordleApi';
  import { KEYBOARD_ROWS, markSymbol } from './wordleState';

  let {
    keyMarks,
    disabled,
    onpress,
  }: { keyMarks: Record<string, Mark>; disabled: boolean; onpress: (key: string) => void } = $props();

  function keyLabel(key: string): string {
    return key === 'ENTER' ? 'Enter guess' : key === 'BACKSPACE' ? 'Delete letter' : key;
  }

  function markLabel(mark: Mark | undefined): string {
    if (mark === 'correct') return ', correct position';
    if (mark === 'present') return ', in the word, wrong position';
    if (mark === 'absent') return ', not in the word';
    return '';
  }
</script>

<div class="wordle-keyboard" role="group" aria-label="Wordle keyboard">
  {#each KEYBOARD_ROWS as keys, rowIndex (rowIndex)}
    <div class="wordle-key-row">
      {#each keys as key (key)}
        <button
          class="wordle-key"
          class:wordle-key-wide={key.length > 1}
          type="button"
          data-key={key}
          data-mark={keyMarks[key] ?? 'none'}
          {disabled}
          aria-label={keyLabel(key) + markLabel(keyMarks[key])}
          onclick={() => onpress(key)}
        >
          {#if key === 'BACKSPACE'}⌫{:else}{key}{/if}
          {#if keyMarks[key]}
            <span class="wordle-key-mark" aria-hidden="true">{markSymbol(keyMarks[key])}</span>
          {/if}
        </button>
      {/each}
    </div>
  {/each}
</div>
