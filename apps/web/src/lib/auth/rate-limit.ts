/**
 * Small in-memory failure cap per client address. This is deliberately simple:
 * a demo-scale gate that makes volume guessing pointless without adding a
 * dependency or a shared store. Counters are per process and reset on restart.
 */
/**
 * Key a failure by the forwarded client when the platform supplies one, so a
 * shared proxy socket does not become one global bucket. `ADDRESS_HEADER` +
 * `XFF_DEPTH` remain the strict adapter-node configuration; this is the
 * best-effort fallback for deployments that only set X-Forwarded-For.
 */
export function failureKey(request: Request, socketAddress: string): string {
  const forwarded = request.headers.get('x-forwarded-for')?.split(',')[0]?.trim();
  return forwarded ? `${socketAddress}|${forwarded}` : socketAddress;
}

export interface RateLimiter {
  blocked(key: string): boolean;
  fail(key: string): void;
  reset(key: string): void;
}

interface Attempt {
  count: number;
  resetAt: number;
}

export function createRateLimiter(
  options: { limit?: number; windowMs?: number; now?: () => number } = {},
): RateLimiter {
  const limit = options.limit ?? 10;
  const windowMs = options.windowMs ?? 15 * 60_000;
  const now = options.now ?? Date.now;
  const attempts = new Map<string, Attempt>();

  function current(key: string): Attempt | undefined {
    const attempt = attempts.get(key);
    if (attempt && now() >= attempt.resetAt) {
      attempts.delete(key);
      return undefined;
    }
    return attempt;
  }

  return {
    blocked(key) {
      const attempt = current(key);
      return !!attempt && attempt.count >= limit;
    },
    fail(key) {
      const at = now();
      const attempt = current(key);
      if (attempt) attempt.count += 1;
      else attempts.set(key, { count: 1, resetAt: at + windowMs });
    },
    reset(key) {
      attempts.delete(key);
    },
  };
}
