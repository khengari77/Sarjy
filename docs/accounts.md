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
stored. Every `/v1/*` route except sign-up, sign-in and sign-out is refused without a live session, by a
gate that denies by default and again by the route's own dependency. Signing out revokes the session on the
server. A page request whose session cannot be checked answers 503; it never opens the app and never
drops the cookie.

Old identities and their data are untouched. Nothing signs anyone in to them; new accounts start empty.

## Limits

Attempts, not calls: per network address, per email and address, per email, and sign-ups per address (see
`app/domain/accounts.py`). Addresses and emails are stored only as keyed digests and pruned after a day. The
web app takes the visitor's address from `ADDRESS_HEADER`; **without it everyone behind the proxy shares one
limit**. Supabase applies its own limits to the API's address; when it throttles, sign-in answers "not
available right now".

## Turning it on

1. Supabase → Authentication → Email: provider on, **Confirm email off** (the built-in mailer only reaches
   project members). With it on, sign-up answers "still has to be confirmed" and opens no session.
2. Back up the database, rehearse on the copy, then `uv run alembic upgrade head` (`0016_accounts` only adds
   three tables; its test proves every existing row survives the upgrade and the downgrade).
3. API: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (a secret or service-role key is refused).
4. Web: `ADDRESS_HEADER` as the host's proxy sets it.
5. Deploy API and web with the switch off, then set it on both: API first, web second. Between the two the
   app answers 401; nothing is lost.

**Going back:** set the switch to `false` on both. The tables stay. Do not downgrade the migration while
accounts exist: it drops them.

## Not included

Password reset and email confirmation need a mail sender. Until then, reset a password in the Supabase
dashboard. Changing a password does not end existing Sarjy sessions; to end them, set
`account_sessions.revoked_at`, or `accounts.disabled_at` to close the account.
