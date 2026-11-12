#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
existing=$(git config --local --get core.hooksPath || true)
if [[ -n "$existing" && "$existing" != '.githooks' ]]; then
  echo "Existing hooksPath ($existing) preserved; integrate the repository hooks manually." >&2
  exit 1
fi
for hook in pre-commit pre-push; do
  if [[ -z "$existing" && -f "$(git rev-parse --git-common-dir)/hooks/$hook" ]]; then
    echo "Existing $hook hook preserved; integrate the quality check manually." >&2
    exit 1
  fi
done
git config --local core.hooksPath .githooks
printf '%s\n' 'Installed repository pre-commit and pre-push checks. This setting also applies to linked worktrees.'
