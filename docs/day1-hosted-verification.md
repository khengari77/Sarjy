# Day 1 hosted operational verification

Recorded 11 November 2026. Public development access remains password-protected.

| Component | Verified revision / result |
|---|---|
| Source release | `4a80697a97b25e4f55275d27b31193fa58c93fa8`; main CI passed |
| API deployment branch | `e7a1830`; CranL deployment `lOVr8Jt5LKQwOsh0w23Qr` succeeded |
| Frontend deployment branch | `33f04c4`; CranL deployment `nIbSMSoDPH9zm9j5kbizL` succeeded |
| Database | Alembic `0004_voice_cleanup`; additive nullable confirmation field |
| Worker | `CA_SynZMEdJgW3o`, version `e3fF7wyjBVZZ`, Running in eu-central |
| Access | API health 200; unauthenticated API and frontend 401 |
| Persistence | Authenticated conversation creation 201 and reload/readback passed |

## Migration and recovery

Private application-table export saved before migration, ignored and permission 0600. Existing application records and ledger values were compared after migration and remained unchanged. Schema change invalidated the verifier's old prepared statement cache; verification passed through a fresh connection.

One expired active reservation was marked ended using an expiry-checked update; expiry and quota history were preserved. Dry run selected three terminal historical sessions. Apply completed all three; LiveKit confirmed their rooms absent. Final dry run selected zero sessions. Exact ledger comparison was unchanged.

A zero-inference hosted lifecycle check created a session in 8.120 seconds. End returned `cleanup: complete`; repeated end returned `already_complete`. No recorded voice or microphone replay was used. An initial probe identity race was corrected to wait for page initialization; it failed before reserving a voice session.

## Speech/weather smoke and spending

Synthetic typed input, using real hosted generation/TTS: 'Ready to help' completed and played audio; a short Tripoli, Libya weather question produced a stored Open-Meteo result and audible response. This establishes typed-input speech, not human microphone or listening quality.

Before operations: OpenAI reservations $0.14; ElevenLabs 92 characters. Migration/reconciliation/zero-inference lifecycle left these unchanged. After the typed smoke: OpenAI $0.20; ElevenLabs 129 characters. The smoke reserved $0.06 and 37 additional characters. Reservations are conservative guards, not vendor invoices. No counters were reset or refunded.

## Remaining gates

Procedure for the unchecked gates below: [Day 1 human acceptance runbook](day1-acceptance-runbook.md), with the
[pending evidence record](day1-acceptance-evidence.md).

- [ ] Human microphone transcript, audible replies and voice/typed continuity.
- [ ] Human confirmation of interruption and mute behavior.
- [ ] Reconnect, refresh and permission-recovery acceptance without manual teardown concealing failures.
- [ ] Final evidence/checklist reconciliation and Day 1 release marker.

Issue #2 is complete; issue #3 remains in progress. No Day 1 completion claim is made yet.

PR #23 migration ancestry is corrected in e288a80: one head `0005_day2_context`, parent `0004_voice_cleanup`. SQLite migration tests: 4 passed, 1 skipped; disposable pgvector PostgreSQL migration tests: 5 passed. Existing contract review findings remain open; PR #23/#24 are unmerged. No Day 2 migration was applied to hosted Supabase.

Local raw verification artifacts remain under ignored artifacts/deployment; never commit exports, credentials, recording files, or tokens.
