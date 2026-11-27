/**
 * Errors that end the call or need a fresh session. They keep the error
 * overlay and its reconnect action; every other error is a notice over the
 * live phase so a denied microphone cannot paint the orb red for the rest of
 * the call.
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

export function isFatalVoiceError(code: string): boolean {
  return FATAL_VOICE_ERRORS.has(code);
}

export function errorCode(error: unknown): string {
  if (error instanceof Error) {
    if (/^[A-Z0-9_]+$/.test(error.message)) return error.message;
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError') return 'MICROPHONE_PERMISSION_DENIED';
    if (error.name === 'NotFoundError' || error.name === 'OverconstrainedError') return 'MICROPHONE_NOT_FOUND';
  }
  return 'API_UNAVAILABLE';
}
