/**
 * Resolve a browser-facing API path to the internal upstream target.
 *
 * SvelteKit decodes route parameters once, so a request such as
 * ``v1/%252e%252e/internal`` arrives here as ``v1/%2e%2e/internal``. A literal
 * ``..`` check is not enough: ``new URL`` decodes ``%2e%2e`` again and
 * normalizes it to ``/internal``. Every encoding layer is therefore decoded
 * and validated before the target is built, and the parsed result is checked
 * to be an unchanged ``/v1/`` path on the configured upstream origin.
 */

const PROXY_PREFIX = 'v1/';
const MAX_DECODE_PASSES = 3;
const SEGMENT_PATTERN = /^[A-Za-z0-9._~-]+$/;

function hasForbiddenCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if (character === '\\' || code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

function decode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

function unsafe(candidate: string): boolean {
  return (
    !candidate.startsWith(PROXY_PREFIX) ||
    candidate.includes('..') ||
    candidate.includes('//') ||
    hasForbiddenCharacters(candidate)
  );
}

/** Decode every encoding layer; ``null`` means the path cannot be trusted. */
export function decodeProxyPath(path: string): string | null {
  let candidate = path.endsWith('/') ? path.slice(0, -1) : path;
  for (let pass = 0; pass <= MAX_DECODE_PASSES; pass += 1) {
    if (unsafe(candidate)) return null;
    const decoded = decode(candidate);
    if (decoded === null) return null;
    if (decoded === candidate) return candidate;
    candidate = decoded;
  }
  return null;
}

/** Return the safe upstream URL, or ``null`` when the path must be rejected. */
export function resolveProxyTarget(path: string, upstream: string): URL | null {
  const decoded = decodeProxyPath(path);
  if (decoded === null) return null;
  const segments = decoded.split('/');
  if (!segments.every((segment) => SEGMENT_PATTERN.test(segment))) return null;
  const base = new URL(upstream);
  const target = new URL('/' + decoded, base);
  const rebuilt = '/' + decoded;
  if (target.origin !== base.origin || target.pathname !== rebuilt) return null;
  return target;
}
