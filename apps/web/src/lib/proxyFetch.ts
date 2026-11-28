/** One upstream attempt's inputs. The body is an ArrayBuffer so it can be resent. */
export type ProxyFetchRequest = {
  url: URL;
  method: string;
  headers: Headers;
  body?: ArrayBuffer;
  timeoutMs: number;
};

const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const UNAVAILABLE_LOG_INTERVAL_MS = 10_000;
const UUID_SEGMENT = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Mutating POSTs that may be repeated without an idempotency key because the
 * API implements them as idempotent transitions of stored state:
 * `POST /v1/memories/{id}/hide` and `/unhide` call `_transition` in
 * apps/api/app/services/memory_visibility.py, which returns the target status
 * with `changed: false` for a repeated call (409 only when the row is deleted
 * or in an unexpected state). Every other POST (session creation, auth
 * exchanges, context confirm/undo, memory write/correct) is never repeated.
 */
const REPEATABLE_POST_PATH = /^v1\/memories\/[^/]+\/(hide|unhide)$/;

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
 * GET, DELETE, POST carrying an idempotency key and POSTs in the safe
 * idempotent-by-state allow-list may be repeated. Any other POST may have
 * reached the API without a way to deduplicate it, so it is never retried.
 */
function isRepeatable(request: ProxyFetchRequest): boolean {
  return (
    request.method === 'GET' ||
    request.method === 'DELETE' ||
    (request.method === 'POST' &&
      (request.headers.has('idempotency-key') || REPEATABLE_POST_PATH.test(request.url.pathname.replace(/^\//, ''))))
  );
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

/**
 * Make the upstream request, retrying once immediately when the first attempt
 * fails on the flaky web-to-API leg: a thrown connection error (a stale
 * kept-alive socket is dropped by undici, so the retry opens a healthy one) or
 * an ingress 502/503/504. Timeouts are not retried; the caller already waited
 * the deadline.
 */
export async function proxyFetch(request: ProxyFetchRequest): Promise<Response> {
  const startedAt = Date.now();
  const repeatable = isRepeatable(request);
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
        signal: AbortSignal.timeout(request.timeoutMs),
      });
    } catch (error) {
      const reason = isTimeout(error) ? 'timeout' : 'network';
      if (reason === 'network' && repeatable && attempts === 1) continue;
      const failure: FailureDetails = { reason };
      const name = errorName(error);
      const code = errorCode(error);
      if (name) failure.name = name;
      if (code) failure.code = code;
      warnFailed(request, failure, attempts, Date.now() - startedAt);
      throw error;
    }
    if (!RETRYABLE_STATUSES.has(response.status)) return response;
    if (repeatable && attempts === 1) {
      await discard(response);
      continue;
    }
    warnFailed(request, { reason: 'status', status: response.status }, attempts, Date.now() - startedAt);
    return response;
  }
}
