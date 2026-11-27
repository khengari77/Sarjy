/**
 * Errors that end the call or need a fresh session. They keep the error
 * overlay and its reconnect action; every other error is a notice over the
 * live phase so a denied microphone cannot paint the orb red for the rest of
 * the call.
 *
 * Produced today: CONNECTION_LOST (transport/startup) and
 * SESSION_RELEASE_FAILED (failed release). The microphone, transcript and
 * budget codes below are recoverable. SESSION_ENDED, SESSION_EXPIRED,
 * TOKEN_EXPIRED, ACCOUNT_SESSION_REQUIRED, HTTP_401 and HTTP_403 are reserved:
 * no web producer exists yet, and this PR deliberately does not add one.
 */
const FATAL_VOICE_ERRORS = new Set([
  'CONNECTION_LOST',
  'SESSION_ENDED',
  'SESSION_EXPIRED',
  'SESSION_RELEASE_FAILED',
  'TOKEN_EXPIRED',
  'ACCOUNT_SESSION_REQUIRED',
  'HTTP_401',
  'HTTP_403',
]);

/** Recoverable errors caused by the agent/transport, not the local microphone. */
const AGENT_RECOVERABLE_ERRORS = new Set([
  'TRANSCRIPT_UNAVAILABLE',
  'DAILY_BUDGET_EXHAUSTED',
  'BUDGET_SERVICE_UNAVAILABLE',
]);

export function isFatalVoiceError(code: string): boolean {
  return FATAL_VOICE_ERRORS.has(code);
}

export function isMicrophoneErrorCode(code: string): boolean {
  return code.startsWith('MICROPHONE_');
}

export function isAgentRecoverableError(code: string): boolean {
  return AGENT_RECOVERABLE_ERRORS.has(code);
}

/**
 * Fatal codes may only be replaced by another fatal code. Clearing is an
 * explicit recovery path and never goes through this helper.
 */
export function setVoiceError(state: { voiceErrorCode: string }, code: string): void {
  if (isFatalVoiceError(state.voiceErrorCode) && !isFatalVoiceError(code)) return;
  state.voiceErrorCode = code;
}

/** A failed microphone operation must keep the retry path visible. */
export function microphoneErrorCode(error: unknown): string {
  const code = errorCode(error);
  return isMicrophoneErrorCode(code) ? code : 'MICROPHONE_UNAVAILABLE';
}

export function errorCode(error: unknown): string {
  if (error instanceof Error) {
    if (/^[A-Z0-9_]+$/.test(error.message)) return error.message;
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError') return 'MICROPHONE_PERMISSION_DENIED';
    if (error.name === 'NotFoundError' || error.name === 'OverconstrainedError') return 'MICROPHONE_NOT_FOUND';
  }
  return 'API_UNAVAILABLE';
}
