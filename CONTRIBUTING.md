# Contributing to Sarjy

## Required agent workflow

Claim one available GitHub issue before editing. Comment with agent identifier, branch, worktree path and scope. Each active issue requires its own branch and worktree. Do not work in the shared primary checkout.

```sh
git fetch origin
git worktree add -b agent/issue-<number>-<slug> ~/Programming/Sarjy-worktrees/issue-<number>-<agent-id> origin/main
cd ~/Programming/Sarjy-worktrees/issue-<number>-<agent-id>
pwd
git branch --show-current
git status --short
```

Never reuse another agent's branch/worktree, stash/reset/clean their changes, force-push, prune worktrees, or alter deploy/api and deploy/web release branches/worktrees. Keep your worktree until review finishes. Start another issue in a fresh worktree.

## Shared-machine resources

Use worktree-local dependencies, build outputs, evidence and disposable databases. Never copy the main .env or production database. Use unique development ports; record process IDs and stop only your processes. Never use killall or kill an unknown port owner.

Browser tests currently bind ports 8000 and 4173. Reserve them in the coordination issue and serialize tests:

```sh
flock /tmp/sarjy-browser-tests.lock pnpm --dir apps/web test:browser
```

Do not run development servers on these reserved ports. Worktrees share Git refs and cloud resources: isolation is not permission to deploy, migrate hosted databases, use secrets or make paid requests. Those actions belong to the coordinator unless explicitly assigned. Never create independent spending ledgers for shared provider keys.

## Scope, contracts and testing

Read the Version 5 architecture and docs/execution_checklist_v5.md. Follow explicit issue dependencies; wait for prerequisite PRs to be reviewed and merged. Do not silently stack branches or merge another agent's branch. Report contract conflicts to the coordinator.

Preserve functional-core/imperative-shell design, immutable domain models and meaningful Hypothesis properties. Mock external boundaries in normal CI. Preserve the $1/day OpenAI ceiling, the configurable TTS daily character allowance (`DEEPGRAM_DAILY_CHARACTERS`, default 50000, #151, #175), shared durable reservations and DeepSeek funding gate. English is the baseline; Arabic remains experimental until qualification. Development stays protected. Day 4 permits revision only.

Relevant checks (run from your own worktree):

```sh
uv sync --locked
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web check
pnpm --dir apps/web build
uv run pytest -q
flock /tmp/sarjy-browser-tests.lock pnpm --dir apps/web test:browser
```

Do not claim hosted voice acceptance from text tests or worker registration. Keep secrets, recordings, local databases and generated artifacts ignored.

## Mandatory PR and review gate

Implement only assigned scope, verify acceptance, inspect exact staged paths/diff, then commit and push your issue branch. Do not add Co-Authored-By trailers. Open a PR targeting main and mark the issue needs-review. Draft PRs must identify incomplete criteria.

**Authors MUST NOT merge their own PRs, push directly to main, enable auto-merge, deploy changes or declare integration complete. The coordinating assistant must review the PR and acceptance evidence first. Passing CI is necessary but does not authorize merging. Changes after review require re-review.**

PR descriptions must include linked issue/checklist IDs, problem and resulting behavior, scope/contracts/migrations, criterion-by-criterion evidence, commands/results, limitations and unrun checks. Do not auto-close issues in PR text; the coordinator closes them after reviewed integration and required acceptance. Stop at review handoff and leave your worktree intact.


### Final-commit bug review

Every merge requires a written bug review tied to the exact final commit SHA, separate from test execution. Review changed code, callers, compatibility and failure paths; record findings and reproduction evidence in the PR. Resolve all blocking findings and require passing CI for that SHA. Changes after review invalidate approval and require review of the new head. Green tests alone never authorize merging.

## Style contract

Read [STYLE.md](STYLE.md) before editing. CI enforces the configured formatting, lint and scoped typing rules.

Install both local hooks with `bash scripts/install_hooks.sh` after updating to the standards baseline. Do not bypass them. The pre-push hook requires bash 4.4 or newer, refuses dirty trees, untracked files in source trees, refs other than HEAD and missing `apps/web/node_modules` for web changes; tag refs under `refs/tags/` are skipped. It runs the quality gate and then only the pytest files affected by what the pushed commits change compared with main (files a merge of main brings are tested there). Changes to `tests/conftest.py`, `pyproject.toml` or `uv.lock` select every non-excluded pytest file, and `alembic.ini` selects `tests/test_migrations.py`. For changed `apps/web/src` TypeScript/Svelte files it runs one related Vitest command, or `pnpm --dir apps/web test:unit` when `apps/web/package.json`, `apps/web/pnpm-lock.yaml` or a Vite/Vitest/Svelte config changes; CSS, Playwright and `apps/web/server.js` changes run no unit tests. It warns when more than 60 pytest files are selected but still runs them. Run `pnpm --dir apps/web install --frozen-lockfile` before pushing frontend changes so those Vitest commands can execute.

Local hooks are bypassable (`--no-verify` or not installing them), so they never replace CI or final-SHA review. The hooks do not run the PostgreSQL suites (`*_postgres.py`), `tests/test_web_access.py`, `tests/test_web_accounts.py`, the browser/Playwright suites, or the complete pytest suite; those run only in CI. Every PR must include the hook confirmation and final-SHA quality/type results from the PR template.
