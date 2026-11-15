export function errorCode(error: unknown): string {
  if (error instanceof Error) {
    if (/^[A-Z0-9_]+$/.test(error.message)) return error.message;
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError') return 'MICROPHONE_PERMISSION_DENIED';
    if (error.name === 'NotFoundError' || error.name === 'OverconstrainedError') return 'MICROPHONE_NOT_FOUND';
  }
  return 'API_UNAVAILABLE';
}
