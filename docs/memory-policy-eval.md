# Memory policy evaluation set

`tests/fixtures/memory_policy_cases.json` holds 250 labelled utterances for the memory policy. The set exists so a
later model-based classifier can be compared against the current keyword-only floor and deterministic parser
(`classify_sensitivity` + `parse_capture`) on the same cases. Every secret value is obviously fake (`0000`,
`ghp_example...`, `examplewifi`).

## Labels

- **save** — a lasting personal fact about the speaker at normal sensitivity. Examples: name, city, job, preferences,
  family structure, and false-refusal traps such as "I collect pin badges".
- **ask** — a lasting fact that needs consent: health, finance, a named third party's private detail, a negation or
  update of something already stored, or low confidence.
- **skip** — no lasting fact to store: credentials and secrets, tasks and reminders, questions, chit-chat, quotations,
  hypotheticals, and other people's trivia. A credential is never saved and never asked about.

Each case carries `tier` (`normal`, `third_party`, `health`, `finance`, `credential`), `negated`, `language` (`en`,
`ar`), `category` (used for counting) and a short `note`. Categories and minimum counts are:

| category | minimum | includes |
| --- | ---: | --- |
| ordinary | 40 | names, cities, jobs, preferences, family structure |
| credential | 30 | 12 Arabic; several phrasings absent from the fixed keyword floor |
| health | 25 | diagnoses, medication, therapy, pregnancy |
| finance | 20 | salary, debt, savings, rent, taxes |
| third_party | 20 | named other people's private details |
| negation | 25 | retractions and updates of stored facts |
| false_refusal | 20 | must-save facts that contain scary words |
| not_fact | 25 | tasks, questions, chit-chat, quotes, hypotheticals |
| arabic | 30 | ordinary and sensitive Arabic facts |

The one deliberate ambiguity is "The password policy at work is strict." It is tagged **skip** in `not_fact`: it is a
general workplace statement, not a personal fact, and it contains no secret value, so it should not be stored or
treated as a credential. The note records that reasoning.

## Running the baseline

```sh
uv run python scripts/memory_policy_baseline.py --out docs/data/memory-policy-baseline.json
```

The script is offline: it reads the fixture, imports only `app.domain.memory_capture` and
`app.domain.memory_policy`, and writes JSON. It does not touch the database or the network. Decisions are computed in
`auto` mode. `parse_capture` supplies `explicit` and `negated`; a rejected capture becomes `skip`.

## Current numbers

Baseline: 250 cases (87 save, 104 ask, 59 skip).

| metric | n | hits | rate | 95% Wilson |
| --- | ---: | ---: | ---: | --- |
| credential recall (tier) | 32 | 15 | 46.9% | 30.9% – 63.5% |
| false refusal (the credential floor blocks a save) | 87 | 3 | 3.5% | 1.2% – 9.7% |
| missed save (the parser finds no fact) | 87 | 59 | 67.8% | 57.4% – 76.7% |
| overall decision accuracy | 250 | 99 | 39.6% | 33.7% – 45.8% |

Credential recall by language: English 15/20 (75.0%), Arabic 0/12 (0.0%).

How to read these. The keyword floor rarely blocks a fact it should keep (3.5%), but the fixed-shape parser recognises
only about a third of the facts a user states (the rest are left to the model tools and the observer), and the floor
misses over half of the credentials, every Arabic one included. The `credential` category's 90.6% decision accuracy
is misleading for the same reason: an unrecognised credential also ends as `skip`, so recall is the number to watch.

| category | n | correct | accuracy |
| --- | ---: | ---: | ---: |
| ordinary | 42 | 14 | 33.3% |
| credential | 32 | 29 | 90.6% |
| health | 26 | 1 | 3.9% |
| finance | 20 | 5 | 25.0% |
| third_party | 20 | 8 | 40.0% |
| negation | 26 | 5 | 19.2% |
| false_refusal | 25 | 12 | 48.0% |
| not_fact | 27 | 25 | 92.6% |
| arabic | 32 | 0 | 0.0% |

Decision accuracy by language: English 87/206 (42.2%), Arabic 12/44 (27.3%).

| true tier | normal | third_party | health | finance | credential |
| --- | ---: | ---: | ---: | ---: | ---: |
| normal (139) | 130 | 1 | 3 | 0 | 5 |
| third_party (25) | 6 | 14 | 3 | 2 | 0 |
| health (30) | 15 | 0 | 15 | 0 | 0 |
| finance (24) | 12 | 1 | 0 | 11 | 0 |
| credential (32) | 16 | 0 | 0 | 1 | 15 |

The low overall accuracy is the honest size of the keyword + parser gap, not a bug in the set: the English parser only
recognises a few first-person shapes, the keyword floor misses most health and finance paraphrases, the fixed
credential floor misses login/token/safe-code and card-PIN phrasings, and every Arabic case falls outside both. Those
misses are exactly what the model-based classifier must improve on.

## Tests

```sh
uv run pytest -q -p no:cacheprovider tests/test_memory_policy_cases.py tests/test_memory_policy.py
```

The fixture tests enforce the schema and enums, unique casefolded texts, the per-category minimums, Arabic letters in
every Arabic case, credential labelling, ask/save/skip policies for the sensitive and not-fact categories, and that the
baseline script runs offline and emits the expected report keys.
