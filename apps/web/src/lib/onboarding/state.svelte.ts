import { createOnboardingController, initialOnboardingState } from './onboarding';

/** Component-scoped reactive onboarding surface; keeps `+page` wiring small. */
export function createOnboarding() {
  const state = $state(initialOnboardingState());
  const controller = createOnboardingController(state);
  return { state, controller };
}

export type OnboardingSurface = ReturnType<typeof createOnboarding>;
