/** One upstream attempt's inputs. The body is an ArrayBuffer so it can be resent. */
export type ProxyFetchRequest = {
  url: URL;
  method: string;
  headers: Headers;
  body?: ArrayBuffer;
  timeoutMs: number;
};

export type ProxyFetchOptions = {
  /** Jittered delay before retry `retry` (1 or 2). Tests inject `() => 0`. */
  statusRetryDelayMs?: (retry: number) => number;
  /** Injectable clock for the total retry-budget guard. Tests pin elapsed time. */
  nowMs?: () => number;
};

const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const UNAVAILABLE_LOG_INTERVAL_MS = 10_000;
const UUID_SEGMENT = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_ATTEMPTS = 3;
const RETRY_MIN_REMAINING_MS = 1_000;

/** Default jitter bounds per retry: attempt 2 waits 150-300 ms, attempt 3 500-900 ms. */
export const STATUS_RETRY_BOUNDS_MS = {
  1: { min: 150, max: 300 },
  2: { min: 500, max: 900 },
} as const;

/**
 * Mutating POSTs that may be repeated after a lost response. This is an exact
 * allow-list of API pathnames the server implements idempotently; a client
 * header or body field can never widen it. Everything else stays
 * non-repeatable, in particular `/v1/conversations`, `/v1/voice/sessions`,
 * auth, reviewer, context confirm/undo, memory write/correct and wordle.
 */
const REPEATABLE_POST_PATHS = [
  // routes/memory.py hide/unhide -> memory_visibility._transition answers changed:false on a repeat.
  /^v1\/memories\/[^/]+\/(hide|unhide)$/,
  // routes/voice.py end_voice -> session_cleanup.cleanup_room answers already_complete after the first cleanup.
  /^v1\/voice\/sessions\/[^/]+\/end$/,
  // routes/voice.py intro_complete -> intro.mark_intro_seen inserts ON CONFLICT DO NOTHING, then updates conditionally.
  /^v1\/voice\/sessions\/[^/]+\/intro-complete$/,
  // routes/memory.py undo -> memory_auto.undo_change answers changed:false once the change is already UNDONE.
  /^v1\/changes\/[^/]+\/undo$/,
] as const;

let lastUnavailableLogAt: number | undefined;
let suppressedUnavailableLogs = 0;

type FailureDetails = {
  reason: 'timeout' | 'network' | 'status';
  status?: number;
  name?: string;
  code?: string;
};

function errorName(error: unknown): string | undefined {
  try {
    if (typeof error !== 'object' || error === null || !('name' in error)) return undefined;
    return typeof error.name === 'string' ? error.name : undefined;
  } catch {
    return undefined;
  }
}

function errorCode(error: unknown): string | undefined {
  try {
    if (typeof error !== 'object' || error === null) return undefined;
    const cause = 'cause' in error ? error.cause : undefined;
    const candidate =
      typeof cause === 'object' && cause !== null && 'code' in cause
        ? cause.code
        : 'code' in error
          ? error.code
          : undefined;
    return typeof candidate === 'string' ? candidate : undefined;
  } catch {
    return undefined;
  }
}

function isTimeout(error: unknown): boolean {
  const name = errorName(error);
  return name === 'TimeoutError' || name === 'AbortError';
}

/**
 * GET, DELETE and POSTs whose normalized path is in the exact allow-list may
 * be repeated. Any other POST may have reached the API without a way to
 * deduplicate it, so it is never retried.
 */
function isRepeatable(request: ProxyFetchRequest): boolean {
  if (request.method === 'GET' || request.method === 'DELETE') return true;
  if (request.method !== 'POST') return false;
  const path = request.url.pathname.replace(/^\//, '');
  return REPEATABLE_POST_PATHS.some((pattern) => pattern.test(path));
}

function diagnosticPath(pathname: string): string {
  return pathname
    .split('/')
    .map((segment) => (UUID_SEGMENT.test(segment) ? ':id' : segment))
    .join('/');
}

/**
 * One structured warning per outage, rate-limited so a failing API cannot flood
 * the web log. Never include the query string, body, headers, cookies, secret,
 * URL host or error message.
 */
function warnFailed(request: ProxyFetchRequest, details: FailureDetails, attempts: number, ms: number): void {
  const now = Date.now();
  if (lastUnavailableLogAt !== undefined && now - lastUnavailableLogAt < UNAVAILABLE_LOG_INTERVAL_MS) {
    suppressedUnavailableLogs += 1;
    return;
  }
  lastUnavailableLogAt = now;
  const suppressed = suppressedUnavailableLogs;
  suppressedUnavailableLogs = 0;
  const fields = {
    method: request.method,
    path: diagnosticPath(request.url.pathname),
    ...details,
    attempts,
    ms,
  };
  console.warn('[Sarjy] api proxy failed', suppressed > 0 ? { ...fields, suppressed } : fields);
}

async function discard(response: Response): Promise<void> {
  try {
    await response.body?.cancel();
  } catch {
    // The retry must proceed even when the discarded body cannot be cancelled.
  }
}

function statusRetryDelay(retry: number): number {
  const bounds = retry <= 1 ? STATUS_RETRY_BOUNDS_MS[1] : STATUS_RETRY_BOUNDS_MS[2];
  const span = bounds.max - bounds.min + 1;
  return bounds.min + Math.floor(Math.random() * span);
}

function wait(ms: number): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Make the upstream request, retrying up to twice when an attempt fails on the
 * flaky web-to-API leg: a thrown connection error, or an ingress 502/503/504.
 * The first connection retry is immediate because undici has already dropped
 * the stale socket; every later retry waits a jittered delay (150-300 ms for
 * the second attempt, 500-900 ms for the third) so a deploying ingress can
 * settle. Timeouts are not retried; the caller already waited the deadline.
 * Every attempt shares the original per-request deadline: a retry starts only
 * with at least 1 s left, and its abort signal is shortened to that remainder.
 */
export async function proxyFetch(request: ProxyFetchRequest, options: ProxyFetchOptions = {}): Promise<Response> {
  const now = options.nowMs ?? Date.now;
  const startedAt = now();
  const deadline = startedAt + request.timeoutMs;
  const repeatable = isRepeatable(request);
  const retryDelay = options.statusRetryDelayMs ?? statusRetryDelay;
  const remainingBudget = (): number => deadline - now();
  const canStartAnother = (attempts: number): boolean =>
    repeatable && attempts < MAX_ATTEMPTS && remainingBudget() >= RETRY_MIN_REMAINING_MS;
  let attempts = 0;
  for (;;) {
    attempts += 1;
    let response: Response;
    try {
      response = await fetch(request.url, {
        method: request.method,
        headers: request.headers,
        body: request.body,
        redirect: 'manual',
        signal: AbortSignal.timeout(Math.min(request.timeoutMs, remainingBudget())),
      });
    } catch (error) {
      const reason = isTimeout(error) ? 'timeout' : 'network';
      if (reason === 'network' && canStartAnother(attempts)) {
        if (attempts === 1) continue;
        await wait(retryDelay(attempts));
        if (canStartAnother(attempts)) continue;
      }
      const failure: FailureDetails = { reason };
      const name = errorName(error);
      const code = errorCode(error);
      if (name) failure.name = name;
      if (code) failure.code = code;
      warnFailed(request, failure, attempts, now() - startedAt);
      throw error;
    }
    if (!RETRYABLE_STATUSES.has(response.status)) return response;
    if (canStartAnother(attempts)) {
      const delay = retryDelay(attempts);
      // Keep the upstream response body if the wait would leave too little
      // budget to start another attempt; otherwise the caller sees an empty 502.
      if (remainingBudget() - delay >= RETRY_MIN_REMAINING_MS) {
        await discard(response);
        await wait(delay);
        if (canStartAnother(attempts)) continue;
      }
    }
    warnFailed(request, { reason: 'status', status: response.status }, attempts, now() - startedAt);
    return response;
  }
}
