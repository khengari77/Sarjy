<script lang="ts">
  import type { ConversationState } from '../voice/conversations';
  import type { CatalogState } from '../voice/catalog';
  import type { VoiceState } from '../voice/connection';
  import OnboardingOverlay from './OnboardingOverlay.svelte';
  import type { OnboardingSurface } from './state.svelte';

  let {
    onboarding,
    busy,
    catalog,
    voice,
    conversation,
  }: {
    onboarding: OnboardingSurface;
    busy: boolean;
    catalog: CatalogState;
    voice: VoiceState;
    conversation: ConversationState;
  } = $props();

  const seenTurns = new Set<string>();
  let armed = false;

  $effect(() => {
    const stage = onboarding.state.progress?.stage;
    if (!stage || stage === 'completed' || stage === 'skipped') {
      armed = false;
      return;
    }
    const turns = conversation.turns;
    if (!armed) {
      // Existing history is context, not an answer to a question asked now.
      for (const turn of turns) seenTurns.add(turn.id);
      armed = true;
      return;
    }
    for (const turn of turns) {
      if (turn.role !== 'user' || seenTurns.has(turn.id)) continue;
      seenTurns.add(turn.id);
      if (turn.status !== 'completed') continue;
      const echo = onboarding.typedEchoes.indexOf(turn.text);
      if (echo >= 0) {
        onboarding.typedEchoes.splice(echo, 1);
        continue;
      }
      void onboarding.controller.handleVoiceUtterance(
        { final: true, speaker: 'user', text: turn.text, id: turn.id },
        catalog.voices,
      );
    }
  });

  const currentVoiceName = $derived(
    voice.activeVoiceName || catalog.voices.find((entry) => entry.id === catalog.voiceId)?.name || '',
  );

  $effect(() => {
    // The worker marks the fixed greeting after real TTS finishes; poll only
    // while that greeting can still change and a call is live.
    if (onboarding.state.progress?.stage !== 'greeting' || !voice.live) return;
    const timer = setInterval(() => void onboarding.controller.load(), 2500);
    return () => clearInterval(timer);
  });
</script>

<OnboardingOverlay
  progress={onboarding.state.progress}
  {busy}
  error={onboarding.state.error}
  voices={catalog.voices}
  {currentVoiceName}
  live={voice.live !== null}
  onname={(name) => void onboarding.controller.submitName(name)}
  onvoice={(next) => void onboarding.controller.chooseVoice(next)}
  onacknowledge={() => void onboarding.controller.acknowledgeVoice()}
  onpace={(pace) => void onboarding.controller.choosePace(pace)}
  ongreeting={() => void onboarding.controller.greetingShown()}
  onfinish={() => void onboarding.controller.finish()}
  onskip={() => void onboarding.controller.skip()}
  onsuspend={() => void onboarding.controller.suspend()}
  onresume={() => void onboarding.controller.resume()}
/>
