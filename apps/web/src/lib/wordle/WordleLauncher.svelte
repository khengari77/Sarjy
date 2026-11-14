<script lang="ts">
  import { onMount } from 'svelte';
  import type { VoiceEvent, VoiceRuntime } from '../audio/voice-runtime';
  import { createWordleGame, type WordleGame } from './wordleGame.svelte';
  import { createWordleVoice, speakWordleReply, type WordleVoice } from './wordleVoice';
  import WordleEntry from './WordleEntry.svelte';
  import WordleStage from './WordleStage.svelte';

  let { compact = false, runtime }: { compact?: boolean; runtime?: VoiceRuntime } = $props();

  const scopeKey = 'sarjy-wordle-voice-scope';

  function readScope(): string {
    try {
      const existing = sessionStorage.getItem(scopeKey);
      if (existing) return existing;
      const value =
        typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
          ? crypto.randomUUID().slice(0, 12)
          : `voice-${Date.now().toString(36)}`;
      sessionStorage.setItem(scopeKey, value);
      return value;
    } catch {
      return 'voice-session';
    }
  }

  const game: WordleGame = createWordleGame();
  // Per-tab idempotency scope; sessionStorage survives reloads so a retried
  // spoken guess replays the same server request instead of consuming a guess.
  let scope = readScope();

  function speak(text: string) {
    // The worker speaks the exact command result through sarjy.say; the same
    // text stays visible and in the single polite live region. Neither path
    // touches the conversational LLM or conversation history.
    void speakWordleReply(text, {
      announce: (reply) => {
        game.state.voiceReply = reply;
        game.state.announcement = reply;
      },
      say: runtime ? (reply) => runtime.say(reply) : undefined,
    });
  }

  function revealPending(pending: boolean) {
    if (!runtime) return;
    void runtime.sendControl({ type: 'wordle.reveal_pending', value: pending }).catch(() => {});
  }

  const voice: WordleVoice = createWordleVoice({ game, speak, scope: () => scope, onRevealPending: revealPending });

  function handleVoiceEvent(event: VoiceEvent) {
    // The adapter itself re-checks final/speaker; the runtime also emits
    // assistant and partial segments, which must never dispatch game actions.
    void voice.handleEvent(event);
  }

  $effect(() => {
    const active = runtime;
    if (!active) return;
    return active.subscribe(handleVoiceEvent);
  });

  onMount(() => {
    if (import.meta.env.DEV) {
      const debug = {
        finalize: (input: { text: string; segmentId?: string; epoch?: number; final?: boolean; speaker?: string }) =>
          voice.handleEvent({
            type: 'transcript',
            text: input.text,
            final: input.final ?? true,
            speaker: (input.speaker as 'user' | 'assistant') ?? 'user',
            segmentId: input.segmentId ?? `dev-${Date.now()}`,
            epoch: input.epoch ?? 1,
          }),
        spoken: () => game.state.voiceReply,
        state: () => ({ view: game.state.view, open: game.state.open, guesses: game.state.guesses.length }),
      };
      (window as unknown as { __sarjyWordleVoice?: typeof debug }).__sarjyWordleVoice = debug;
    }
  });
</script>

{#if compact}
  <div class="wordle-quick-entry"><WordleEntry onopen={game.open} /></div>
{:else}
  <WordleEntry onopen={game.open} />
{/if}

{#if game.state.open}
  <WordleStage {game} />
{/if}
