# Jev-classified UI commands (#168)

Paraphrased UI requests ("could you brighten this up", "talk a bit quicker") that miss the exact-phrase grammar are classified by Jev in one request, so the main model never has to claim it cannot change settings.

Order inside `handle_finalized_utterance`: memory handling, exact grammar, voice-name/gender grammar, then Jev, then the unsupported fallback. An exact match never calls Jev. Text over 160 characters or 25 words, and yes/no confirmations, never call Jev.

One `POST /v1/systemone` carries seven choice questions (`route`, `family`, `theme`, `captions`, `speed`, `voice`, `memory_op`), defined in `app/domain/ui_routing.py`. The pure evaluator acts only when the `family` and that family's detail answer are each at least 0.85 probable and agree, and `route` is not a confident (>= 0.85) `conversation`. Live evaluation showed `route` is weak for polite phrasings, so it only vetoes; `multiple_commands` still needs route >= 0.85:

- theme / captions / speed produce `SetTheme` / `SetCaptionsVisible` / `AdjustSpeakingSpeed` (+-0.1) and reuse the exact-command effect path.
- voice male/female reuses the gendered-voice path; `named`/`other` fall through.
- `multiple_commands` answers "I can only make one change at a time".
- Everything else (chat, negation, low confidence, mismatch) falls through to the main model.

Failures never block a turn: a missing key, open circuit (3 failures, 5 minute bypass), budget denial, 1200 ms timeout (`UI_DEADLINE_SECONDS`, separate from the 750 ms routing deadline because seven questions are slower), HTTP error or malformed body all return no classification.

Configuration (names only; see `.env.example`): `JEV_API_KEY` (empty disables), `JEV_COMMANDS_ENABLED`, `JEV_DAILY_REQUESTS` (default 2000). Each request reserves 1 unit of the durable `jev` ledger (`requests`, Tripoli day) in the API process before sending.

`memory_op` (remember | forget | recall | restore | none) is parsed and exposed on the result but not acted on yet; a follow-up will use it to force the main model's memory tools.

Wording: the shared scope text and the per-option criteria count polite requests and ability questions ("can you / could you / would you ...", "bring back / go back to X") as commands, while statements, opinions, questions about a feature, negations, other people's behaviour and edits to the user's own text are not. Live check: `scripts/jev_ui_eval.py` over `tests/fixtures/jev_ui_live_cases.json` (manual, reads `JEV_API_KEY` from the environment, never run in CI).

## Forced memory tools (#166)

`memory_op` from the same single classification call (shared with the worker through `/internal/agent/command`, never a second Jev call) decides memory requests. When it is `remember`, `forget` or `recall` at >= 0.85, the worker forces `propose_memory`, `propose_forget` or `recall` as the `tool_choice` of the FIRST model inference of that turn only; the continuation after the tool result is never forced. A forced call carries no prose, so nothing is spoken before the deterministic confirmation question. `restore`, `none`, low confidence, a missing classification or any Jev failure force nothing and behave as before. The model still supplies the fact or query through the tool arguments, and all deterministic validation, signed proposals, one-shot consent and decision-turn guards are unchanged.

When a classifier is configured, the regex capture at turn start interprets only the exact "remember that <clause>" (or "don't forget that <clause>") shape. "Remember to ...", "Remember when ...", "Remember me" and every other free-form request produce no regex proposal. With no classifier (key missing, flag off) the previous regex behaviour is kept. Live check: `scripts/jev_memory_eval.py` over `tests/fixtures/jev_memory_live_cases.json`.
