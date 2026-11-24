/** Account mode policy with no environment or network: paths, messages and cookie parsing. */
export const ACCOUNT_COOKIE = 'sarjy_account';

export type AccountAction = 'sign-in' | 'sign-up';

export interface IssuedSession {
  token: string;
  maxAge: number;
}

const ISSUED = /^sarjy_account=([A-Za-z0-9_-]{43})(?:;|$)/;
const MAX_AGE = /;\s*max-age=(\d{1,9})(?:;|$)/i;

const MESSAGES: Record<string, string> = {
  ACCOUNT_CREDENTIALS_INVALID: 'That email and password do not match an account.',
  ACCOUNT_EMAIL_UNCONFIRMED: 'This email still has to be confirmed. Check your inbox, then sign in.',
  ACCOUNT_EXISTS: 'An account with this email already exists. Sign in instead.',
  ACCOUNT_PASSWORD_WEAK: 'That password is too easy to guess. Choose a longer one.',
  ACCOUNT_INPUT_INVALID: 'Enter a valid email and a password of at least 8 characters.',
  ACCOUNT_ATTEMPT_LIMIT: 'Too many attempts. Wait a few minutes and try again.',
  ACCOUNT_OAUTH_FAILED: 'Sign-in with that provider did not complete. Try again.',
};
export const ACCOUNT_UNAVAILABLE = 'Sign-in is not available right now. Try again shortly.';

const OAUTH_FAILURES: Record<string, string> = {
  failed: 'ACCOUNT_OAUTH_FAILED',
  unavailable: 'ACCOUNT_SERVICE_UNAVAILABLE',
  limit: 'ACCOUNT_ATTEMPT_LIMIT',
};

const OAUTH_PATH = /^\/auth\/(callback|google|apple|github|azure|facebook)$/;

/** Reachable without a session. Everything else that renders a page needs one. */
export function isAccountPublicPath(pathname: string): boolean {
  return pathname === '/sign-in' || pathname === '/sign-up' || OAUTH_PATH.test(pathname);
}

/** Copy for a coded failure; an unknown code never reaches the page. */
export function accountMessage(code: unknown): string {
  return typeof code === 'string' && Object.hasOwn(MESSAGES, code) ? MESSAGES[code] : ACCOUNT_UNAVAILABLE;
}

/** Copy for the `?oauth=` outcome codes on the entry pages; anything else is silent. */
export function oauthMessage(value: unknown): string {
  return typeof value === 'string' && Object.hasOwn(OAUTH_FAILURES, value) ? accountMessage(OAUTH_FAILURES[value]) : '';
}

/** The session the API just issued, or `null` when its response did not carry exactly one. */
export function issuedSession(setCookies: readonly string[]): IssuedSession | null {
  const issued = setCookies.filter((value) => value.startsWith(`${ACCOUNT_COOKIE}=`));
  if (issued.length !== 1) return null;
  const token = ISSUED.exec(issued[0])?.[1];
  const maxAge = Number(MAX_AGE.exec(issued[0])?.[1]);
  return token && maxAge > 0 ? { token, maxAge } : null;
}
