<script lang="ts">
  import { onMount } from 'svelte';
  import { errorCode } from '../voice/errors';
  import { createWordleClient, type GameStatus, type WordleGuess, type WordleStats as Stats } from './wordleApi';
  import {
    buildBoard,
    describeGuess,
    formatCountdown,
    formatPuzzleDate,
    friendlyError,
    keyboardMarks,
    nextPuzzleCountdown,
    requestId as nextRequestId,
    statusText,
    WORDLE_TIMEZONE,
  } from './wordleState';
  import WordleBoard from './WordleBoard.svelte';
  import WordleHeader from './WordleHeader.svelte';
  import WordleKeyboard from './WordleKeyboard.svelte';
  import WordlePanel from './WordlePanel.svelte';
  import WordleStats from './WordleStats.svelte';
  import WordleStatus from './WordleStatus.svelte';
  import './wordle.css';

  let { onclose }: { onclose: () => void } = $props();

  type View = 'loading' | 'playing' | 'won' | 'lost' | 'unavailable' | 'error';

  const client = createWordleClient();
  let view = $state<View>('loading');
  let puzzleDate = $state('');
  let puzzleTimezone = $state<string | undefined>(undefined);
  let answer = $state<string | undefined>(undefined);
  let guesses = $state<WordleGuess[]>([]);
  let attemptsRemaining = $state<number | undefined>(undefined);
  let draft = $state('');
  let notice = $state('');
  let announcement = $state('');
  let stats = $state<Stats | null>(null);
  let statsUnavailable = $state(false);
  let busy = $state(false);
  let pendingId = $state<string | null>(null);
  let pendingWord = $state<string | null>(null);
  let revealedRow = $state(-1);
  let now = $state(new Date());
  let stageEl = $state<HTMLDivElement>();
  let previouslyFocused: HTMLElement | null = null;
  let loadToken = 0;

  const accepting = $derived(view === 'playing');
  const board = $derived(buildBoard(guesses, draft, accepting));
  const keyMarks = $derived(keyboardMarks(guesses));
  const timezone = $derived(puzzleTimezone ?? WORDLE_TIMEZONE);
  const countdown = $derived(formatCountdown(nextPuzzleCountdown(now, timezone)));
  const dateLabel = $derived(puzzleDate ? formatPuzzleDate(puzzleDate) : '');
  const remaining = $derived(attemptsRemaining ?? Math.max(0, 6 - guesses.length));

  onMount(() => {
    previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    stageEl?.focus({ preventScroll: true });
    void load();
    const timer = setInterval(() => (now = new Date()), 1000);
    return () => clearInterval(timer);
  });

  async function loadStats(token = loadToken) {
    statsUnavailable = false;
    try {
      const value = await client.stats();
      if (token === loadToken) stats = value;
    } catch {
      if (token !== loadToken) return;
      stats = null;
      statsUnavailable = true;
    }
  }
  async function load() {
    const token = ++loadToken;
    view = 'loading';
    notice = '';
    announcement = '';
    guesses = [];
    stats = null;
    statsUnavailable = false;
    attemptsRemaining = undefined;
    try {
      const today = await client.today();
      if (token !== loadToken) return;
      puzzleDate = today.date;
      puzzleTimezone = today.timezone;
      answer = today.answer;
      guesses = today.game?.guesses ?? [];
      attemptsRemaining = today.game?.attemptsRemaining;
      revealedRow = -1;
      draft = '';
      pendingId = null;
      pendingWord = null;
      if (!today.available) {
        view = 'unavailable';
        notice = friendlyError(today.code ?? 'WORDLE_UNAVAILABLE');
        return;
      }
      const status: GameStatus = today.game?.status ?? 'in_progress';
      view = status === 'in_progress' ? 'playing' : status;
      const loaded = status === 'in_progress' ? 'Puzzle loaded.' : '';
      announcement = [loaded, statusText(status, guesses.length, answer, remaining)].filter(Boolean).join(' ');
      void loadStats(token);
    } catch (error) {
      if (token !== loadToken) return;
      const code = errorCode(error);
      view =
        code === 'WORDLE_UNAVAILABLE' || code === 'WORDLE_DISABLED' || code === 'WORDLE_TIMEZONE_INVALID'
          ? 'unavailable'
          : 'error';
      notice = friendlyError(code);
    }
  }

  function close() {
    onclose();
    setTimeout(() => previouslyFocused?.focus(), 0);
  }

  async function submit() {
    if (view !== 'playing' || busy) return;
    if (draft.length < 5) {
      notice = 'Enter a five-letter word.';
      announcement = notice;
      return;
    }
    const word = draft;
    // Reuse an id only for a retry of the same word; a new word is a new guess.
    const requestId = pendingWord === word && pendingId ? pendingId : nextRequestId();
    pendingId = requestId;
    pendingWord = word;
    busy = true;
    notice = '';
    try {
      const result = await client.guess(word, requestId);
      const next = result.game?.guesses ?? [...guesses, { word, marks: result.marks }];
      const status = result.game?.status ?? result.status;
      guesses = next;
      attemptsRemaining = result.game?.attemptsRemaining;
      view = status === 'in_progress' ? 'playing' : status;
      answer = result.answer ?? answer;
      revealedRow = next.length - 1;
      draft = '';
      pendingId = null;
      pendingWord = null;
      const spoken = next[revealedRow] ? describeGuess(next[revealedRow]) : '';
      announcement = [spoken, statusText(status, next.length, answer, remaining)].filter(Boolean).join(' ');
      if (status !== 'in_progress') void loadStats();
    } catch (error) {
      // Keep the draft so the player can fix or resend it; the id is only
      // reused while the word is unchanged.
      notice = friendlyError(errorCode(error));
      announcement = notice;
    } finally {
      busy = false;
    }
  }

  function pressKey(key: string) {
    if (view !== 'playing' || busy) return;
    if (key === 'ENTER') {
      void submit();
    } else if (key === 'BACKSPACE') {
      if (draft) draft = draft.slice(0, -1);
      pendingId = null;
      pendingWord = null;
      notice = '';
    } else if (draft.length < 5) {
      draft += key.toUpperCase();
      pendingId = null;
      pendingWord = null;
      notice = '';
    }
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
      void submit();
    } else if (event.key === 'Backspace') {
      event.preventDefault();
      pressKey('BACKSPACE');
    } else if (/^[a-zA-Z]$/.test(event.key)) {
      event.preventDefault();
      pressKey(event.key);
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
    data-view={view}
    tabindex="-1"
    bind:this={stageEl}
  >
    <WordleHeader {dateLabel} {puzzleDate} {countdown} {timezone} onclose={close} />

    {#if view === 'loading' || view === 'unavailable' || view === 'error'}
      <WordlePanel {view} {notice} onretry={() => void load()} />
    {:else}
      {#if stats || statsUnavailable}<WordleStats {stats} unavailable={statsUnavailable} />{/if}

      <WordleBoard {board} {revealedRow} />

      <WordleStatus
        view={view === 'playing' ? 'playing' : view}
        {notice}
        attempts={guesses.length}
        {answer}
        {remaining}
      />

      <WordleKeyboard {keyMarks} disabled={view !== 'playing' || busy} onpress={pressKey} />
    {/if}

    <p class="sr-only" id="wordle-help">
      Type with your keyboard or the on-screen keys. Enter submits a guess; Backspace deletes a letter. Escape closes
      the game and keeps your progress.
    </p>
    <p class="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>
  </div>
</div>
