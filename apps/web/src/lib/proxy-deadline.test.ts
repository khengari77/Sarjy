import { describe, expect, it } from 'vitest';
import { PROXY_TIMEOUT_MS, SESSION_START_TIMEOUT_MS, proxyTimeoutMs } from './proxy-deadline';

describe('proxyTimeoutMs', () => {
  it('uses the long deadline only for starting a voice session', () => {
    expect(proxyTimeoutMs('v1/voice/sessions', 'POST')).toBe(SESSION_START_TIMEOUT_MS);
    expect(proxyTimeoutMs('v1/voice/sessions/123/end', 'POST')).toBe(PROXY_TIMEOUT_MS);
    expect(proxyTimeoutMs('v1/voice/sessions', 'GET')).toBe(PROXY_TIMEOUT_MS);
  });
});
