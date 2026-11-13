import { describe, expect, it } from 'vitest';
import { decodeProxyPath, resolveProxyTarget } from './proxy-path';

const UPSTREAM = 'http://127.0.0.1:8000';

describe('decodeProxyPath', () => {
  it.each([
    'v1/%2e%2e/internal/example',
    'v1/%2E%2E/internal/example',
    'v1/%252e%252e/internal/example',
    'v1/%25252e%25252e/internal/example',
    'v1/..%2finternal',
    'v1/..%5cinternal',
    'v1//internal',
    'v1/internal%00/example',
    'v1/%zz',
  ])('rejects traversal input %s', (path) => {
    expect(decodeProxyPath(path)).toBeNull();
  });

  it.each(['v1/conversations', 'v1/conversations/abc-123/turns', 'v1/voice/sessions/abc/end'])(
    'accepts a normal path %s',
    (path) => {
      expect(decodeProxyPath(path)).toBe(path);
    },
  );

  it('decodes a benign encoding layer', () => {
    expect(decodeProxyPath('v1/%76oices')).toBe('v1/voices');
  });
});

describe('resolveProxyTarget', () => {
  it('builds an upstream URL on the configured origin', () => {
    const target = resolveProxyTarget('v1/conversations/abc-123', UPSTREAM);
    expect(target?.origin).toBe(UPSTREAM);
    expect(target?.pathname).toBe('/v1/conversations/abc-123');
  });

  it.each([
    'v1/%2e%2e/internal/example',
    'v1/%252e%252e/internal/example',
    'v1/..%2finternal',
    'v1//internal',
    'v1/%2e%2e/%2e%2e/internal',
    'v1evil/internal',
    '',
    'internal/example',
  ])('refuses %s', (path) => {
    expect(resolveProxyTarget(path, UPSTREAM)).toBeNull();
  });

  it('never moves the target off the upstream origin', () => {
    const target = resolveProxyTarget('v1/conversations', UPSTREAM);
    expect(target?.host).toBe('127.0.0.1:8000');
  });
});
