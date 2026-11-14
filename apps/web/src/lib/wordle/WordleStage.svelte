<script lang="ts">
  import { onMount } from 'svelte';
  import type { WordleGame } from './wordleGame.svelte';
  import {
    buildBoard,
    formatCountdown,
    formatPuzzleDate,
    keyboardMarks,
    nextPuzzleCountdown,
    WORDLE_TIMEZONE,
  } from './wordleState';
  import WordleBoard from './WordleBoard.svelte';
  import WordleHeader from './WordleHeader.svelte';
  import WordleKeyboard from './WordleKeyboard.svelte';
  import WordlePanel from './WordlePanel.svelte';
  import WordleStats from './WordleStats.svelte';
  import WordleStatus from './WordleStatus.svelte';
  import './wordle.css';

  let { game }: { game: WordleGame } = $props();

  let now = $state(new Date());
  let stageEl = $state<HTMLDivElement>();
  let previouslyFocused: HTMLElement | null = null;

  const gameState = $derived(game.state);
  const accepting = $derived(gameState.view === 'playing');
  const board = $derived(buildBoard(gameState.guesses, gameState.draft, accepting));
  const keyMarks = $derived(keyboardMarks(gameState.guesses));
  const timezone = $derived(gameState.puzzleTimezone ?? WORDLE_TIMEZONE);
  const countdown = $derived(formatCountdown(nextPuzzleCountdown(now, timezone)));
  const dateLabel = $derived(gameState.puzzleDate ? formatPuzzleDate(gameState.puzzleDate) : '');
  const remaining = $derived(gameState.attemptsRemaining ?? Math.max(0, 6 - gameState.guesses.length));

  onMount(() => {
    previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    stageEl?.focus({ preventScroll: true });
    void game.ensureLoaded();
    const timer = setInterval(() => (now = new Date()), 1000);
    return () => clearInterval(timer);
  });

  function close() {
    game.close();
    setTimeout(() => previouslyFocused?.focus(), 0);
  }

  function handleKeydown(event: KeyboardEvent) {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target as HTMLElement | null;
    const editing =
      target !== null && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
    if (editing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    // Let a focused button or link handle its own activation.
    if (event.key === 'Enter' && target?.closest('button, a')) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      game.pressKey('ENTER');
    } else if (event.key === 'Backspace') {
      event.preventDefault();
      game.pressKey('BACKSPACE');
    } else if (/^[a-zA-Z]$/.test(event.key)) {
      event.preventDefault();
      game.pressKey(event.key);
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div
  class="wordle-backdrop"
  role="presentation"
  onclick={(event) => {
    if (event.target === event.currentTarget) close();
  }}
>
  <div
    class="wordle-stage"
    role="dialog"
    aria-modal="false"
    aria-labelledby="wordle-title"
    aria-describedby="wordle-help"
    data-view={gameState.view}
    tabindex="-1"
    bind:this={stageEl}
  >
    <WordleHeader {dateLabel} puzzleDate={gameState.puzzleDate} {countdown} {timezone} onclose={close} />

    {#if gameState.view === 'loading' || gameState.view === 'unavailable' || gameState.view === 'error'}
      <WordlePanel view={gameState.view} notice={gameState.notice} onretry={() => void game.load()} />
    {:else}
      {#if gameState.stats || gameState.statsUnavailable}
        <WordleStats stats={gameState.stats} unavailable={gameState.statsUnavailable} />
      {/if}

      <WordleBoard {board} revealedRow={gameState.revealedRow} />

      <WordleStatus
        view={gameState.view === 'playing' ? 'playing' : gameState.view}
        notice={gameState.notice}
        attempts={gameState.guesses.length}
        answer={gameState.answer}
        {remaining}
      />

      {#if gameState.voiceReply}<p class="wordle-voice" data-voice="true">{gameState.voiceReply}</p>{/if}

      <WordleKeyboard {keyMarks} disabled={gameState.view !== 'playing' || gameState.busy} onpress={game.pressKey} />
    {/if}

    <p class="sr-only" id="wordle-help">
      Type with your keyboard or the on-screen keys. Enter submits a guess; Backspace deletes a letter. Escape closes
      the game and keeps your progress. You can also say play Wordle, guess a five-letter word, ask how many guesses are
      left, or quit Wordle.
    </p>
    <p class="sr-only" aria-live="polite" aria-atomic="true">{gameState.announcement}</p>
  </div>
</div>
