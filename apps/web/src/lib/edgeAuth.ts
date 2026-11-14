/**
 * Web-edge authentication is a separate concern from API protection.
 *
 * `DEVELOPMENT_PROTECTED` keeps the API private (docs off, web-service secret
 * required) and historically also turned on HTTP Basic Auth for the whole web
 * app. Reviewer invitations need the API protection but not the shared web
 * password, so `WEB_EDGE_AUTH_ENABLED` can switch the Basic prompt off without
 * exposing an unauthenticated API.
 */
export function resolveEdgeAuth(flag: string, developmentProtected: string): boolean {
  if (flag === 'true') return true;
  if (flag === 'false') return false;
  // Backwards compatible default: an unset flag follows the old behavior.
  return developmentProtected === 'true';
}

/** Paths that must stay reachable on the invitation credential alone. */
export function edgeAuthRequired(path: string): boolean {
  if (path === '/review' || path.startsWith('/review/')) return false;
  if (path === '/api/v1/reviewer/session') return false;
  return true;
}
