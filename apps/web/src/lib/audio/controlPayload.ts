/** Room data-channel payloads are untrusted: parse them into typed events or drop them. */

import { normalizeCommandResult } from '../commands/commandStore';
import type { VoiceEventBody } from './voice-runtime';

const BUDGET_ERRORS = new Set(['DAILY_BUDGET_EXHAUSTED', 'BUDGET_SERVICE_UNAVAILABLE']);

type ControlEvent = VoiceEventBody;

function decode(text: Uint8Array): Record<string, unknown> | null {
  try {
    const value = JSON.parse(new TextDecoder().decode(text)) as unknown;
    return typeof value === 'object' && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function controlEvent(topic: string | undefined, payload: Uint8Array): ControlEvent | null {
  if (topic === 'sarjy.command') {
    const command = normalizeCommandResult(decode(payload));
    return command ? { type: 'command', command } : null;
  }
  if (topic !== 'sarjy.error') return null;
  const code = decode(payload)?.code;
  return typeof code === 'string' && BUDGET_ERRORS.has(code) ? { type: 'error', code } : null;
}
