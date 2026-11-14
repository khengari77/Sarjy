import { request as apiRequest } from '../api';

export type OnboardingStage =
  | 'not_started'
  | 'greeting'
  | 'preferred_name'
  | 'voice_choice'
  | 'awaiting_voice_ack'
  | 'speaking_pace'
  | 'capability_intro'
  | 'completed'
  | 'skipped'
  | 'suspended';

export type Pace = 'slower' | 'normal' | 'faster';

export interface OnboardingCapabilities {
  speaking_pace: boolean;
  weather: boolean;
  memory: boolean;
  wordle: boolean;
  chat: boolean;
}

export interface OnboardingProgress {
  schema_version: number;
  revision: number;
  stage: OnboardingStage;
  preferred_name: string | null;
  selected_voice_id: string | null;
  selected_pace: Pace | null;
  intro_played_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  suspended_from: OnboardingStage | null;
  last_event_id: string | null;
  greeting: string | null;
  voice_application: 'next_call' | null;
  capabilities: OnboardingCapabilities;
}

export interface OnboardingState {
  status: 'idle' | 'loading' | 'active' | 'error';
  progress: OnboardingProgress | null;
  error: string;
}

export type EventType =
  | 'start'
  | 'greeting_played'
  | 'submit_name'
  | 'choose_voice'
  | 'acknowledge_voice'
  | 'choose_pace'
  | 'acknowledge_capabilities'
  | 'skip'
  | 'suspend'
  | 'resume';

export type EventPayload = { name?: string; voice_id?: string; pace?: Pace };

export function initialOnboardingState(): OnboardingState {
  return { status: 'idle', progress: null, error: '' };
}

export function isTerminal(stage: OnboardingStage | undefined): boolean {
  return stage === 'completed' || stage === 'skipped';
}

export function isSuspended(stage: OnboardingStage | undefined): boolean {
  return stage === 'suspended';
}

export function stepLabel(stage: OnboardingStage): string {
  const steps: Partial<Record<OnboardingStage, string>> = {
    greeting: 'Welcome',
    preferred_name: 'Your name',
    voice_choice: 'Your voice',
    awaiting_voice_ack: 'Your voice',
    speaking_pace: 'Speaking pace',
    capability_intro: 'What you can ask',
  };
  return steps[stage] ?? 'Setup';
}

export function newEventId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `onboarding-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

interface ControllerDeps {
  request: typeof apiRequest;
  nextEventId: () => string;
}

const defaultDeps: ControllerDeps = { request: apiRequest, nextEventId: newEventId };

export function createOnboardingController(state: OnboardingState, deps: Partial<ControllerDeps> = {}) {
  const { request, nextEventId } = { ...defaultDeps, ...deps };

  function apply(progress: OnboardingProgress) {
    state.progress = progress;
    state.status = 'active';
    state.error = '';
  }

  async function load() {
    state.status = state.progress ? state.status : 'loading';
    try {
      apply(await request<OnboardingProgress>('onboarding'));
    } catch (error) {
      state.status = state.progress ? 'active' : 'error';
      state.error = error instanceof Error ? error.message : 'ONBOARDING_UNAVAILABLE';
    }
  }

  async function send(type: EventType, payload: EventPayload = {}) {
    state.error = '';
    try {
      const progress = await request<OnboardingProgress>('onboarding/events', {
        event_id: nextEventId(),
        type,
        ...payload,
      });
      apply(progress);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'ONBOARDING_EVENT_FAILED';
      // A stale tab can hold an old stage; resync instead of trapping the user.
      await load();
      state.error = message;
    }
  }

  function active(): boolean {
    return state.progress !== null && !isTerminal(state.progress.stage) && !isSuspended(state.progress.stage);
  }

  return {
    load,
    send,
    active,
    greetingShown: () => send('greeting_played'),
    submitName: (name: string) => send('submit_name', { name }),
    chooseVoice: (voiceId: string | null) => send('choose_voice', { voice_id: voiceId ?? undefined }),
    acknowledgeVoice: () => send('acknowledge_voice'),
    choosePace: (pace: Pace) => send('choose_pace', { pace }),
    finish: () => send('acknowledge_capabilities'),
    skip: () => send('skip'),
    suspend: () => send('suspend'),
    resume: () => send('resume'),
  };
}

export type OnboardingController = ReturnType<typeof createOnboardingController>;
