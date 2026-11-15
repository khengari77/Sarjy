<script lang="ts">
  import { markSymbol, MAX_ATTEMPTS, tileLabel, WORD_LENGTH, type BoardRow } from './wordleState';

  let { board, revealedRow }: { board: BoardRow[]; revealedRow: number } = $props();
</script>

<div
  class="wordle-board"
  role="grid"
  aria-label="Wordle board, six guesses of five letters"
  aria-rowcount={MAX_ATTEMPTS}
  aria-colcount={WORD_LENGTH}
  tabindex="0"
>
  {#each board as row (row.index)}
    <div class="wordle-row" role="row" aria-rowindex={row.index + 1} data-row={row.index} data-state={row.state}>
      {#each row.tiles as tile (tile.column)}
        <div
          class="wordle-tile"
          class:wordle-tile-fresh={row.index === revealedRow && row.state === 'revealed'}
          class:wordle-tile-filled={tile.letter !== '' && !tile.mark}
          role="gridcell"
          aria-colindex={tile.column + 1}
          aria-label={tileLabel(tile, row.index)}
          data-mark={tile.mark ?? 'none'}
          style:--wordle-column={tile.column}
        >
          <span class="wordle-letter" aria-hidden="true">{tile.letter}</span>
          {#if tile.mark}
            <span class="wordle-symbol" aria-hidden="true">{markSymbol(tile.mark)}</span>
          {/if}
        </div>
      {/each}
    </div>
  {/each}
</div>
