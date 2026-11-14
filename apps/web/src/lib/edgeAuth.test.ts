import { describe, expect, it } from 'vitest';
import { edgeAuthRequired, resolveEdgeAuth } from './edgeAuth';

describe('edge authentication', () => {
  it('explicit WEB_EDGE_AUTH_ENABLED wins over DEVELOPMENT_PROTECTED', () => {
    expect(resolveEdgeAuth('false', 'true')).toBe(false);
    expect(resolveEdgeAuth('true', 'false')).toBe(true);
  });

  it('an unset flag keeps the previous behavior', () => {
    expect(resolveEdgeAuth('', 'true')).toBe(true);
    expect(resolveEdgeAuth('', 'false')).toBe(false);
  });

  it('reviewer landing and exchange stay open when edge auth is on', () => {
    expect(edgeAuthRequired('/review')).toBe(false);
    expect(edgeAuthRequired('/review/')).toBe(false);
    expect(edgeAuthRequired('/api/v1/reviewer/session')).toBe(false);
    expect(edgeAuthRequired('/api/v1/conversations')).toBe(true);
    expect(edgeAuthRequired('/')).toBe(true);
  });

  it('reviewer mode with edge auth off needs no Authorization header', () => {
    // This is the reviewer-demo matrix: DEVELOPMENT_PROTECTED=true keeps the
    // API private, WEB_EDGE_AUTH_ENABLED=false drops the shared web password.
    const edgeAuth = resolveEdgeAuth('false', 'true');
    expect(edgeAuth).toBe(false);
    // With edge auth off the hook never inspects Authorization for any path.
    expect(edgeAuth && edgeAuthRequired('/api/v1/conversations')).toBe(false);
  });
});
