#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
existing=$(git config --local --get core.hooksPath || true)
if [[ -n "$existing" && "$existing" != '.githooks' ]]; then
  echo "Existing hooksPath ($existing) preserved; integrate .githooks/pre-push manually." >&2
  exit 1
fi
if [[ -z "$existing" && -f "$(git rev-parse --git-common-dir)/hooks/pre-push" ]]; then
  echo 'Existing pre-push hook preserved; integrate the quality check manually.' >&2
  exit 1
fi
git config --local core.hooksPath .githooks
printf '%s\n' 'Installed repository pre-push checks. This setting also applies to linked worktrees.'
