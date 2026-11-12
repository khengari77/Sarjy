#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
output=$(mktemp)
trap 'rm -f "$output"' EXIT
status=0
uv run --locked mypy --no-incremental "$@" >"$output" 2>&1 || status=$?
cat "$output"
if [[ "$status" -ne 0 ]]; then
  exit "$status"
fi
# Mypy can emit configuration warnings without returning a failure status.
if grep -Ei '(^|[[:space:]])warning:' "$output" >/dev/null; then
  echo 'Mypy warnings are errors.' >&2
  exit 1
fi
