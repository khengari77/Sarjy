export const PROXY_TIMEOUT_MS = 15_000;
export const SESSION_START_TIMEOUT_MS = 45_000;
const SESSION_START_PATH = 'v1/voice/sessions';

/**
 * Session creation waits for LiveKit dispatch and is slower than normal API
 * traffic. Everything else keeps the shorter deadline so failures stay visible.
 */
export function proxyTimeoutMs(path: string, method: string): number {
 return method === 'POST' && path === SESSION_START_PATH ? SESSION_START_TIMEOUT_MS : PROXY_TIMEOUT_MS;
}
