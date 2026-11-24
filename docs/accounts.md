# Accounts

Email and password sign-up and sign-in (#205). Off by default; `ACCOUNT_ACCESS_ENABLED=true` on **both** the
API and the web app turns it on and replaces the shared token and the reviewer invitations. The two are
mutually exclusive: either process refuses to start with `REVIEWER_ACCESS_ENABLED=true`.

## Who does what

| Part | Owns |
| --- | --- |
| Supabase Auth | Passwords. Sarjy never stores or logs one, and discards the provider's tokens. |
| API (`/v1/auth/*`) | The account, its one identity, revocable 30-day sessions, attempt limits. |
| Web app | The `/sign-in` and `/sign-up` pages, the page check, the cookie. Works without JavaScript. |

The browser holds only `sarjy_account`: a random, `HttpOnly`, `Secure`, `SameSite=Lax` token whose SHA-256 is
stored. Every `/v1/*` route except sign-up, sign-in, sign-out and the OAuth code exchange (`/v1/auth/oauth/exchange`) is refused without a live session, by a
gate that denies by default and again by the route's own dependency. Signing out revokes the session on the
server; if the API cannot be reached then, the browser forgets the cookie and the session ends at its expiry.
Ended sessions are deleted at the account's next sign-in. A page request whose session cannot be checked answers 503; it never opens the app and never
drops the cookie.

Old identities and their data are untouched. Nothing signs anyone in to them; new accounts start empty.

## Limits

Attempts, not calls: failures per network address, failures per email and address, and sign-ups per address
(see `app/domain/accounts.py`). A success is given back. Provider sign-ups share the sign-up allowance: 60 per
source address per hour, not given back on success. There is no limit per email alone, because it would
let a stranger lock its owner out; guessing from many addresses is bounded by Supabase's own limits. Addresses and emails are stored only as keyed digests and pruned after a day. The
web app takes the visitor's address from `ADDRESS_HEADER` and refuses to start without it. Supabase applies its own limits to the API's address; when it throttles, sign-in answers "not
available right now".

## Turning it on

1. Supabase → Authentication → Email: provider on, **Confirm email off** (the built-in mailer only reaches
   project members). With it on, sign-up answers "still has to be confirmed" and opens no session.
2. Back up the database, rehearse on the copy, then `uv run alembic upgrade head` (`0016_accounts` only adds
   three tables; its test proves every existing row survives the upgrade and the downgrade).
3. API: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (`sb_publishable_…`; anything else is refused).
4. Web: `ADDRESS_HEADER` as the host's proxy sets it.
5. Deploy API and web with the switch off, then set it on both: API first, web second. Between the two the
   old door still opens but every data request is refused; nothing is lost.

**Going back:** set the switch to `false` on both. The tables stay. Do not downgrade the migration while
accounts exist: it drops them.

## Sign in with Google, Apple and others

Buttons appear on the sign-in and sign-up pages for each name in the web app's `OAUTH_PROVIDERS`
(any of `google,apple,github,azure,facebook`; `azure` is Microsoft). The web app also needs `SUPABASE_URL`.
Supabase does the handshake with the provider; the web app starts it with PKCE and keeps the verifier in a
10-minute `sarjy_oauth` cookie, and the API trades the returned code for the same revocable session a password
sign-in gets (`POST /v1/auth/oauth/exchange`). Supabase links a provider sign-in to an existing account with the
same verified email. A password account is never entered that way: if Supabase reports both an `email` identity
and a provider identity, the exchange is refused and the visitor signs in with the password instead. The web app
sends them back to `/sign-in?oauth=taken` with that instruction.

Per provider, you do these steps once (they need your own console logins; nothing here can be done from code):

1. In Supabase: Authentication → URL Configuration. Add `https://<web host>/auth/callback` to Redirect URLs
   (and `http://localhost:5173/auth/callback` for local work).
2. In Supabase: Authentication → Sign In / Providers → the provider → paste its client id and secret → Save.
   The provider page shows the callback URL (`https://<project>.supabase.co/auth/v1/callback`) to register.
3. In the provider's console, register that Supabase callback URL:
   - **Google**: Google Cloud → APIs & Services → Credentials → OAuth client ID (Web application). Free.
   - **GitHub**: Settings → Developer settings → OAuth Apps. Free.
   - **Microsoft** (`azure`): Entra ID → App registrations; pick "personal accounts and organisations". Free.
   - **Facebook**: Meta for Developers → create an app with Facebook Login. Free, but needs app review to go public.
   - **Apple**: Apple Developer Program (paid, 99 USD a year) → Services ID, a Sign in with Apple key, and a client
     secret that Apple limits to 6 months, so it must be regenerated twice a year.
4. Set `OAUTH_PROVIDERS` and `SUPABASE_URL` on the web app, restart it, and sign in once with each button.

A provider that is not listed in `OAUTH_PROVIDERS` redirects to `/sign-in` rather than opening a handshake. Turn
one off by removing it from the list. When at least one provider is listed, `SUPABASE_URL` must be `https://`
(except `http://localhost` or `http://127.0.0.1` for local development), and the web app refuses to boot otherwise.

## Known trade-off

Sign-up says when an email already has an account, so it can be used to test addresses (60 an hour from one
network address). Hiding it needs email confirmation. Sign-in never says which part was wrong.

## Not included

Password reset and email confirmation need a mail sender. Until then, reset a password in the Supabase
dashboard. Changing a password does not end existing Sarjy sessions; to end them, set
`account_sessions.revoked_at`, or `accounts.disabled_at` to close the account.
