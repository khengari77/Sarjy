/**
 * Resolve a post-login destination. Only same-origin absolute paths survive;
 * protocol-relative, absolute and control-character URLs fall back to `/`.
 */
export function safeNextPath(value: string | null | undefined): string {
  if (!value) return '/';
  if (!value.startsWith('/')) return '/';
  if (value.startsWith('//') || value.startsWith('/\\')) return '/';
  if (value.includes('\\')) return '/';
  if ([...value].some((character) => character.charCodeAt(0) < 0x20 || character.charCodeAt(0) === 0x7f)) return '/';
  try {
    const resolved = new URL(value, 'https://sarjy.invalid');
    if (resolved.origin !== 'https://sarjy.invalid') return '/';
    if (!resolved.pathname.startsWith('/') || resolved.pathname.startsWith('//')) return '/';
    if (resolved.pathname === '/logout') return '/';
    resolved.searchParams.delete('token');
    return `${resolved.pathname}${resolved.search}${resolved.hash}`;
  } catch {
    return '/';
  }
}
