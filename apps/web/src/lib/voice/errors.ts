/**
 * Errors that end the call or need a fresh session. They keep the error
 * overlay and its reconnect action; every other error is a notice over the
 * live phase so a denied microphone cannot paint the orb red for the rest of
 * the call.
 *
 * Produced today: CONNECTION_LOST (transport/startup), GREETING_UNCONNECTED
 * (startup failed after the browser greeting played), SESSION_RELEASE_FAILED
 * (failed release), HTTP_401/HTTP_403 (api.ts response status) and
 * ACCOUNT_SESSION_REQUIRED (api.ts/sessionAttempt/+page). SESSION_ENDED,
 * SESSION_EXPIRED and TOKEN_EXPIRED are reserved: no web producer exists yet,
 * and this PR deliberately does not add one.
 */
const FATAL_VOICE_ERRORS = new Set([
  'CONNECTION_LOST',
  'SESSION_ENDED',
  'SESSION_EXPIRED',
  'SESSION_RELEASE_FAILED',
  'TOKEN_EXPIRED',
  'ACCOUNT_SESSION_REQUIRED',
  'GREETING_UNCONNECTED',
  'HTTP_401',
  'HTTP_403',
]);

export function isFatalVoiceError(code: string): boolean {
  return FATAL_VOICE_ERRORS.has(code);
}

export function isMicrophoneErrorCode(code: string): boolean {
  return code.startsWith('MICROPHONE_');
}

/** Recoverable, non-microphone errors that a healthy agent/interrupt clears. */
export function isAgentClearableError(code: string): boolean {
  return code !== '' && !isFatalVoiceError(code) && !isMicrophoneErrorCode(code);
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
