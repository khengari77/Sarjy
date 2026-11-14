import { createOnboardingController, initialOnboardingState } from './onboarding';

/** Component-scoped reactive onboarding surface; keeps `+page` wiring small. */
export function createOnboarding() {
  const state = $state(initialOnboardingState());
  const controller = createOnboardingController(state);
  // Typed composer turns are echoes, not voice answers; the bridge drops them.
  const typedEchoes: string[] = [];
  return {
    state,
    controller,
    typedEchoes,
    noteTyped(text: string) {
      typedEchoes.push(text);
    },
  };
}

export type OnboardingSurface = ReturnType<typeof createOnboarding>;
