# Code style

Read CONTRIBUTING.md before editing. pyproject.toml is the executable authority.

Use Python 3.12, Ruff formatting (120-character configuration), and Ruff import ordering and correctness rules. Run `uv run ruff check .` and `uv run ruff format --check .`.

Public interfaces need explicit types. Strict mypy currently covers app.domain and the generation boundary only: run `uv run mypy`. Do not introduce unexplained Any or blanket suppressions. Exceptions need a narrow justification and linked issue.

Keep domain decisions pure and domain values immutable. Services own I/O and explicit transactions. Use meaningful names and cohesive functions; test observable behavior and failure paths.

Do not disable checks or lower thresholds to pass CI. Run `uv run pytest -q`, existing frontend checks and required integration checks after updating from main. Follow the exact-final-SHA review requirement in CONTRIBUTING.md.

Preserve existing Svelte/TypeScript conventions. Frontend formatting and architecture enforcement are introduced separately in PR #69.

Install local pre-commit and pre-push enforcement with `bash scripts/install_hooks.sh` after updating your checkout to the standards commit. It runs `bash scripts/check_quality.sh` without rewriting files. Git configuration applies to linked worktrees; update active branches before relying on these hooks. Do not bypass hooks. Local hooks are bypassable and do not replace CI or final-SHA review. GitHub currently cannot require status checks for this private repository under its account plan.

Existing B008 exemptions permit FastAPI dependency defaults. The pipeline computed-field decorator exception is scoped to that module. Dynamic JSON uses explicit Any at serialization boundaries; native provider plugins use Any to support injected test doubles. Keep these boundaries narrow.

Every PR must confirm both hooks are installed and used without bypasses, and report quality/type results for its final SHA. The pre-commit hook requires complete Python/config changes to be staged, so it checks the version being committed. CI checks the committed tree independently.

Warnings are failures: pytest uses `filterwarnings = ["error"]`; mypy strict mode rejects unused ignores, redundant casts and Any returns, and unused configuration warnings are enabled. Do not add warning filters or type suppressions to hide failures without a narrow justification and linked issue.
