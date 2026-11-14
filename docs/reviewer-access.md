# Reviewer access: shared token door and invitation links

How people outside the team get into a protected deployment, and how to create, list and
revoke invitation links. Everything below was read from the code at the commit that
introduced this page; items marked **not built** or **not verified** are called out so a
reader does not assume more than exists.

## The two doors

Development deployments must run with `DEVELOPMENT_PROTECTED=true` (never deploy with
protection disabled). Two ways in, both server-enforced:

| Door | What it is | Revocable per person? |
| --- | --- | --- |
| **Shared token** | One token (`REVIEW_ACCESS_TOKEN`) typed on the token page. A signed session cookie lasts until `REVIEW_ACCESS_EXPIRES_AT`. At most `REVIEW_ACCESS_MAX_USES` browsers are admitted. | No: rotate the token or set the expiry in the past to revoke everyone. |
| **Invitation link** | A one-off link, `https://<site>/review#invitation=<token>`, minted by an operator. Each person who opens it gets an isolated session. | Yes: revoke the invitation. |

Invitations are additive and **off by default**. Set `REVIEWER_ACCESS_ENABLED=true` (the same value
on the API and the web app) to turn them on; `/review` and the invitation exchange then stay reachable
without the shared token, while every other page keeps the shared-token door. Setting it back to
`false` returns to the shared-token behaviour only.

## How a link works

1. The operator mints an invitation. The raw token (32 random bytes) is written once to a new file and
   is never printed; only its SHA-256 hash is stored in the database.
2. A reviewer opens `/review#invitation=<token>`. The token is in the URL fragment, which browsers do
   not send to servers, and the page removes it from the address bar and history before exchanging it.
3. The API checks the invitation (not expired, not revoked, identities left), creates an isolated
   reviewer identity and sets a session cookie. That identity lasts 7 days.
4. Every `/v1/*` request is re-authorised by the API (identity, invitation expiry and revocation, caps).
   The web edge only decides whether to show the token page. A revoked or expired invitation opens no data.
5. In reviewer mode no anonymous identities are created: `/v1/demo-session` is not public.

## Creating, listing and revoking

There is no admin web route. The operator CLI needs access to the API's database (it reads the same
settings as the API, so run it with the deployment's `DATABASE_URL`):

```sh
uv run python scripts/reviewer_invitations.py create \
  --base-url https://<web-host> --link-file /secure/path/invite-1.txt
uv run python scripts/reviewer_invitations.py list
uv run python scripts/reviewer_invitations.py revoke --invitation-id <id>
```

`create` options (defaults):

| Option | Default | Meaning |
| --- | --- | --- |
| `--expires-at` | now + 72 hours | ISO-8601 time **with a UTC offset** |
| `--max-identities` | 8 (max 50) | How many separate reviewers/browsers one link can admit, for its lifetime |
| `--max-calls` | 24 | Total voice calls the link allows |
| `--link-file` | required | Must not already exist; created with mode 0600 |
| `--base-url` | required | Must be an HTTPS origin with no credentials, query or fragment |

The command prints the invitation id, expiry and remaining identities, never the token. `revoke` is
irreversible and takes effect on the next API request. Every denial (expired, revoked, exhausted)
returns the same generic `403 REVIEWER_ACCESS_DENIED`.

## Settings

| Setting | Purpose |
| --- | --- |
| `REVIEWER_ACCESS_ENABLED` | Turns invitation access on (API **and** web, same value). Default `false`. |
| `DEVELOPMENT_PROTECTED` | Must be `true` on hosted deployments. |
| `REVIEW_ACCESS_TOKEN`, `REVIEW_ACCESS_MAX_USES`, `REVIEW_ACCESS_EXPIRES_AT`, `REVIEW_ACCESS_STATE_PATH` | The shared-token door; see the comments in `.env.example`. |
| `ADDRESS_HEADER`, `XFF_DEPTH` | Behind a proxy, which header carries the client address so failure caps are per visitor. |
| `COOKIE_SECURE=true` | Required in production. |

The database must have migration `0014_reviewer_access` applied (`uv run alembic upgrade head`).

## Testing locally

The CLI insists on an HTTPS base URL, so for `http://127.0.0.1` mint the link with any HTTPS host and
edit the host in the generated file by hand; the token after `#invitation=` is the part that matters.
Turn on `REVIEWER_ACCESS_ENABLED=true` for both processes first.

## Known gaps

- **Per-invitation voice allowance: not built.** All reviewers share the deployment's daily ElevenLabs
  character budget (`ELEVENLABS_DAILY_CHARACTERS`, default 2500). A per-invitation allowance with its own
  ledger (and refusal of voice for identity-only sessions) is still open as part of issue #151.
  Until it exists, a few reviewers can exhaust the day's voice for everyone.
- **Hosted state: not verified.** Whether the hosted deployment has this code and migration 0014 applied
  was not checked when this page was written; confirm before sending links.
- **Recovery:** revoke the affected invitation, or set `REVIEWER_ACCESS_ENABLED=false` to fall back to the
  shared-token door. Rotating the shared token or setting `REVIEW_ACCESS_EXPIRES_AT` in the past
  ends shared-token access.
