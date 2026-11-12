#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
