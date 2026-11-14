<script lang="ts">
  import type { CatalogState } from '../voice/catalog';
  import type { VoiceState } from '../voice/connection';
  import OnboardingOverlay from './OnboardingOverlay.svelte';
  import type { OnboardingSurface } from './state.svelte';

  let {
    onboarding,
    busy,
    catalog,
    voice,
  }: {
    onboarding: OnboardingSurface;
    busy: boolean;
    catalog: CatalogState;
    voice: VoiceState;
  } = $props();

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
