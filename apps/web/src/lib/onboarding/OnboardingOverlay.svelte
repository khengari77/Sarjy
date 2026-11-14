<script lang="ts">
  import type { VoiceOption } from '../api';
  import { isSuspended, isTerminal, stepLabel, type OnboardingProgress, type Pace } from './onboarding';
  import './onboarding.css';

  let {
    progress,
    busy = false,
    error = '',
    voices,
    currentVoiceName,
    live,
    onname,
    onvoice,
    onacknowledge,
    onpace,
    ongreeting,
    onfinish,
    onskip,
    onsuspend,
    onresume,
  }: {
    progress: OnboardingProgress | null;
    busy?: boolean;
    error?: string;
    voices: VoiceOption[];
    currentVoiceName: string;
    live: boolean;
    onname: (name: string) => void;
    onvoice: (voiceId: string | null) => void;
    onacknowledge: () => void;
    onpace: (pace: Pace) => void;
    ongreeting: () => void;
    onfinish: () => void;
    onskip: () => void;
    onsuspend: () => void;
    onresume: () => void;
  } = $props();

  let nameDraft = $state('');
  let voiceDraft = $state('');
  let paceDraft = $state<Pace>('normal');
  let touched = $state(false);

  $effect(() => {
    if (!touched) voiceDraft = progress?.selected_voice_id ?? '';
  });

  const visible = $derived(progress !== null && !isTerminal(progress.stage));
  const stage = $derived(progress?.stage);
</script>

{#if visible && progress}
  <aside class="onboarding" aria-label="First-run setup" data-stage={progress.stage}>
    {#if isSuspended(progress.stage)}
      <div class="onboarding-row">
        <p>Setup is paused. Keep chatting, or pick up where you left off.</p>
        <div class="onboarding-actions">
          <button class="ghost-button" type="button" disabled={busy} onclick={onskip}>Skip setup</button>
          <button class="primary-button" type="button" disabled={busy} onclick={onresume}>Continue setup</button>
        </div>
      </div>
    {:else}
      <header class="onboarding-head">
        <p class="onboarding-step">{stepLabel(progress.stage)}</p>
        <button class="ghost-button" type="button" disabled={busy} onclick={onskip}>Skip setup</button>
      </header>

      <div aria-live="polite">
        {#if stage === 'greeting'}
          <p class="onboarding-question">{progress.greeting ?? "Hey, I'm Sarjy. What should I call you?"}</p>
          <p class="muted-text">
            {live ? 'Sarjy is speaking this greeting once.' : 'Tap the cloud to hear it aloud, or continue by typing.'}
          </p>
          <div class="onboarding-actions">
            <button class="primary-button" type="button" disabled={busy} onclick={ongreeting}>
              Continue by typing
            </button>
          </div>
        {:else if stage === 'preferred_name'}
          <form
            class="onboarding-form"
            onsubmit={(event) => {
              event.preventDefault();
              if (nameDraft.trim()) onname(nameDraft.trim());
            }}
          >
            <label class="field-label" for="onboarding-name">What should I call you?</label>
            <input
              id="onboarding-name"
              maxlength="80"
              autocomplete="given-name"
              bind:value={nameDraft}
              placeholder="Your name"
            />
            <button class="primary-button" type="submit" disabled={busy || !nameDraft.trim()}>Save name</button>
          </form>
          <p class="muted-text">You can type anything; voice is optional.</p>
        {:else if stage === 'voice_choice'}
          <p class="onboarding-question">
            {progress.preferred_name ? `Call you: ${progress.preferred_name}` : 'Choose a voice'}
          </p>
          <p class="muted-text">
            Current voice: {currentVoiceName || 'the default voice'}. A different voice applies from the next call; it
            is not switched mid-call here.
          </p>
          {#if voices.length}
            <label class="field-label" for="onboarding-voice">Try another voice</label>
            <select
              id="onboarding-voice"
              bind:value={voiceDraft}
              onchange={() => (touched = true)}
              aria-label="Onboarding voice"
            >
              <option value="">Keep the current voice</option>
              {#each voices as voice (voice.id)}
                <option value={voice.id}>{voice.name}</option>
              {/each}
            </select>
            <div class="onboarding-actions">
              <button class="ghost-button" type="button" disabled={busy} onclick={() => onvoice(null)}>
                Keep this voice
              </button>
              <button
                class="primary-button"
                type="button"
                disabled={busy || !voiceDraft || voiceDraft === progress.selected_voice_id}
                onclick={() => onvoice(voiceDraft)}
              >
                Use selected voice
              </button>
            </div>
          {:else}
            <div class="onboarding-actions">
              <button class="primary-button" type="button" disabled={busy} onclick={() => onvoice(null)}>
                Keep this voice
              </button>
            </div>
            <p class="muted-text">The voice catalog is unavailable, so the current voice stays.</p>
          {/if}
        {:else if stage === 'awaiting_voice_ack'}
          <p class="onboarding-question">The live voice switch has not been confirmed yet.</p>
          <p class="muted-text">You can keep the current voice and let a new one apply next call.</p>
          <div class="onboarding-actions">
            <button class="primary-button" type="button" disabled={busy} onclick={onacknowledge}>
              Continue with the current voice
            </button>
          </div>
        {:else if stage === 'speaking_pace'}
          <p class="onboarding-question">How should I speak?</p>
          <div class="onboarding-choices" role="radiogroup" aria-label="Speaking pace">
            {#each ['slower', 'normal', 'faster'] as option (option)}
              <label class="onboarding-choice">
                <input type="radio" name="pace" value={option} bind:group={paceDraft} />
                <span>{option}</span>
              </label>
            {/each}
          </div>
          <p class="muted-text">Your pace applies from the next call.</p>
          <div class="onboarding-actions">
            <button class="primary-button" type="button" disabled={busy} onclick={() => onpace(paceDraft)}>
              Save pace
            </button>
          </div>
        {:else if stage === 'capability_intro'}
          <p class="onboarding-question">Here is what you can ask.</p>
          <ul class="onboarding-capabilities">
            <li>Ask about the weather.</li>
            <li>Ask what Sarjy remembers.</li>
            <li>Play today's Wordle.</li>
            <li>Chat naturally.</li>
          </ul>
          <div class="onboarding-actions">
            <button class="primary-button" type="button" disabled={busy} onclick={onfinish}>Finish setup</button>
          </div>
        {/if}
      </div>

      {#if error}
        <p class="onboarding-error" role="alert">{error}</p>
      {/if}

      <p class="onboarding-transparency">
        Sarjy remembers what you tell it. <a href="#memory-explorer">Review or forget it in Memory</a>.
      </p>
      {#if !isSuspended(progress.stage)}
        <button class="onboarding-pause" type="button" disabled={busy} onclick={onsuspend}>Pause setup</button>
      {/if}
    {/if}
  </aside>
{/if}
