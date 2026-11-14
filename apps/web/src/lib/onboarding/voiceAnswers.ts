import type { VoiceOption } from '../api';
import type { OnboardingProgress, Pace } from './onboarding';

export interface VoiceUtterance {
  /** Only finalized transcripts may change onboarding. */
  final: boolean;
  speaker: 'user' | 'assistant';
  text: string;
  /** Stable turn/segment id, used to ignore replayed finals. */
  id?: string;
}

export type VoiceOnboardingAction =
  | { type: 'submit_name'; name: string }
  | { type: 'choose_voice'; voiceId: string | null }
  | { type: 'acknowledge_voice' }
  | { type: 'choose_pace'; pace: Pace }
  | { type: 'finish' }
  | { type: 'skip' }
  | { type: 'suspend' }
  | { type: 'resume' }
  | { type: 'ignore' };

const IGNORE: VoiceOnboardingAction = { type: 'ignore' };
const QUESTION_STARTS = ['what', 'where', 'when', 'who', 'why', 'how', 'can', 'could', 'do', 'does', 'is', 'are'];
const NAME_PREFIXES = ['my name is', 'call me', 'i am', "i'm", "it's", 'this is', "name's"];

function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/[“”"'.!?,]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function looksLikeQuestion(text: string): boolean {
  if (text.includes('?')) return true;
  const words = normalize(text).split(' ');
  return words.length > 5 && QUESTION_STARTS.includes(words[0]);
}

function extractName(text: string): string | null {
  let value = text
    .trim()
    .replace(/^["'“”]|["'“”]$/g, '')
    .trim();
  for (const prefix of NAME_PREFIXES) {
    const pattern = new RegExp(`^${prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b[\\s,:-]*`, 'i');
    if (pattern.test(value)) {
      value = value.replace(pattern, '').trim();
      break;
    }
  }
  value = value.replace(/[.!?]+$/, '').trim();
  const words = value.split(/\s+/).filter(Boolean);
  if (!value || value.length > 80 || words.length > 4) return null;
  if (!/[a-z\u00c0-\u024f\u0600-\u06ff]/i.test(value)) return null;
  if (looksLikeQuestion(text)) return null;
  return value;
}

function matchVoice(text: string, voices: VoiceOption[]): string | null | undefined {
  const normalized = normalize(text);
  if (/^(keep|same|current|default|this)( voice)?$/.test(normalized) || normalized.includes('keep this voice')) {
    return null;
  }
  for (const voice of voices) {
    if (normalized === normalize(voice.id) || normalized === normalize(voice.name)) return voice.id;
  }
  if (voices.length === 1 && normalized.includes(normalize(voices[0].name))) return voices[0].id;
  for (const voice of voices) {
    const name = normalize(voice.name);
    if (name.length >= 4 && normalized.includes(name)) return voice.id;
  }
  return undefined;
}

function paceFor(text: string): Pace | null {
  const normalized = normalize(text);
  if (/\b(slower|slow down|less fast)\b/.test(normalized)) return 'slower';
  if (/\b(faster|speed up|quicker|more fast)\b/.test(normalized)) return 'faster';
  if (/\b(normal|regular|default|medium|keep)\b/.test(normalized)) return 'normal';
  return null;
}

function isSkip(text: string): boolean {
  return /^(skip|skip setup|skip onboarding|stop setup|stop onboarding)$/.test(normalize(text));
}

function isPause(text: string): boolean {
  return /^(pause|pause setup|hold on|not now|stop)$/.test(normalize(text));
}

function isResume(text: string): boolean {
  return /^(resume|resume setup|continue setup|continue|unpause|pick up where i left off)$/.test(normalize(text));
}

function isFinish(text: string): boolean {
  return /^(continue|ok|okay|ready|done|finish|finish setup|got it|thanks|thank you)$/.test(normalize(text));
}

/** Pure, conservative mapping from one finalized user utterance to one action. */
export function interpretVoiceAnswer(
  progress: OnboardingProgress,
  utterance: VoiceUtterance,
  voices: VoiceOption[],
): VoiceOnboardingAction {
  if (!utterance.final || utterance.speaker !== 'user' || !utterance.text.trim()) return IGNORE;
  if (progress.stage === 'completed' || progress.stage === 'skipped') return IGNORE;
  const text = utterance.text.trim();
  if (isSkip(text)) return { type: 'skip' };

  switch (progress.stage) {
    case 'suspended':
      return isResume(text) ? { type: 'resume' } : IGNORE;
    case 'greeting':
    case 'preferred_name': {
      if (isPause(text)) return { type: 'suspend' };
      const name = extractName(text);
      return name ? { type: 'submit_name', name } : { type: 'suspend' };
    }
    case 'voice_choice': {
      if (isPause(text)) return { type: 'suspend' };
      const voiceId = matchVoice(text, voices);
      if (voiceId !== undefined) return { type: 'choose_voice', voiceId };
      if (/\b(voice|another|different)\b/.test(normalize(text))) return IGNORE;
      return { type: 'suspend' };
    }
    case 'awaiting_voice_ack':
      if (/\b(continue|current|keep|ok|okay|next)\b/.test(normalize(text))) return { type: 'acknowledge_voice' };
      return IGNORE;
    case 'speaking_pace': {
      if (isPause(text)) return { type: 'suspend' };
      const pace = paceFor(text);
      return pace ? { type: 'choose_pace', pace } : { type: 'suspend' };
    }
    case 'capability_intro':
      return isFinish(text) ? { type: 'finish' } : { type: 'suspend' };
    default:
      return IGNORE;
  }
}
