#!/usr/bin/env bash
set -euo pipefail

if ((BASH_VERSINFO[0] * 100 + BASH_VERSINFO[1] < 404)); then
  echo "scripts/run_push_tests.sh requires bash 4.4 or newer (found $BASH_VERSION)." >&2
  exit 1
fi

cd "$(git rev-parse --show-toplevel)"
root=$(pwd)

entries=()
while IFS=' ' read -r local_ref local_sha remote_ref remote_sha; do
  [[ -n "${local_ref:-}" ]] || continue
  if [[ "$local_ref" == refs/tags/* ]]; then
    continue
  fi
  if [[ "$local_sha" =~ ^0+$ ]]; then
    continue
  fi
  entries+=("$local_sha $remote_sha")
done

if [[ ${#entries[@]} -eq 0 ]]; then
  exit 0
fi

head=$(git rev-parse HEAD)
for entry in "${entries[@]}"; do
  read -r sha _ <<<"$entry"
  if ! sha_commit=$(git rev-parse "${sha}^{commit}" 2>/dev/null); then
    echo "Cannot resolve $sha to a commit; only HEAD ($head) can be tested from this worktree." >&2
    exit 1
  fi
  if [[ "$sha_commit" != "$head" ]]; then
    echo "Refusing to push $sha: only HEAD ($head) can be tested from this worktree." >&2
    exit 1
  fi
done

if ! git diff --quiet HEAD; then
  echo 'Refusing to push with modified or staged tracked files; commit or restore them first.' >&2
  exit 1
fi

untracked_file=$(mktemp)
trap 'rm -f "$untracked_file"' EXIT
git ls-files --others --exclude-standard -z -- apps services scripts tests migrations packages infra .githooks >"$untracked_file"
untracked=()
while IFS= read -r -d '' path; do
  untracked+=("$path")
done <"$untracked_file"
if [[ ${#untracked[@]} -gt 0 ]]; then
  echo 'Refusing to push with untracked files in source trees:' >&2
  printf '  %s\n' "${untracked[@]}" >&2
  exit 1
fi

resolve_base() {
  local sha=$1 remote_sha=$2 base
  if [[ ! "$remote_sha" =~ ^0+$ ]] \
    && git cat-file -e "${remote_sha}^{commit}" 2>/dev/null \
    && git merge-base --is-ancestor "$remote_sha" "$sha" 2>/dev/null; then
    printf '%s\n' "$remote_sha"
    return 0
  fi
  if base=$(git merge-base "$sha" origin/main 2>/dev/null); then
    printf '%s\n' "$base"
    return 0
  fi
  return 1
}

diff_file=$(mktemp)
branch_file=$(mktemp)
both_file=$(mktemp)
tests_file=$(mktemp)
web_file=$(mktemp)
web_unit_file=$(mktemp)
trap 'rm -f "$untracked_file" "$diff_file" "$branch_file" "$both_file" "$tests_file" "$web_file" "$web_unit_file"' EXIT

changed=()
declare -A seen=()
for entry in "${entries[@]}"; do
  read -r sha remote_sha <<<"$entry"
  if ! base=$(resolve_base "$sha" "$remote_sha"); then
    echo "Cannot determine a base commit for $sha; fetch origin/main and try again." >&2
    exit 1
  fi
  if ! git diff --name-only -z --no-renames "$base" "$sha" >"$diff_file"; then
    exit 1
  fi
  # A merge of main brings files CI has already tested there: keep only what this branch itself changes.
  if main_base=$(git merge-base "$sha" origin/main 2>/dev/null); then
    if ! git diff --name-only -z --no-renames "$main_base" "$sha" >"$branch_file"; then
      exit 1
    fi
  else
    cp "$diff_file" "$branch_file"
  fi
  # Not a process substitution: its failure would look like a push that changes nothing.
  sort -z "$diff_file" "$branch_file" | uniq -zd >"$both_file"
  while IFS= read -r -d '' path; do
    if [[ -z "${seen[$path]+set}" ]]; then
      seen[$path]=1
      changed+=("$path")
    fi
  done <"$both_file"
done

for path in "${changed[@]}"; do
  if [[ "$path" == tests/*.py && ! -e "$path" ]]; then
    echo "Deleted test file: $path" >&2
  fi
done

for name in $(compgen -e | grep '^GIT_' || true) \
  PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD PYTHONOPTIMIZE; do
  unset "$name"
done


uv run --locked python scripts/select_tests.py --root "$root" -- "${changed[@]}" >"$tests_file"
uv run --locked python scripts/select_tests.py --root "$root" --web -- "${changed[@]}" >"$web_file"
uv run --locked python scripts/select_tests.py --root "$root" --web-unit -- "${changed[@]}" >"$web_unit_file"

mapfile -t selected <"$tests_file"
mapfile -t web_changed <"$web_file"
web_unit=$(<"$web_unit_file")

echo "Selected ${#selected[@]} test file(s)."
if [[ ${#selected[@]} -gt 60 ]]; then
  echo "Warning: ${#selected[@]} test files selected for this push." >&2
fi
if [[ ${#selected[@]} -gt 0 ]]; then
  echo 'Selected tests:'
  printf '  %s\n' "${selected[@]}"
  uv run --locked pytest -q -p no:cacheprovider "${selected[@]}"
else
  echo 'No Python tests selected.'
fi

web_related=()
for path in "${web_changed[@]}"; do
  if [[ -e "$path" ]]; then
    web_related+=("$path")
  fi
done

if [[ "$web_unit" == "1" || ${#web_related[@]} -gt 0 ]]; then
  if [[ ! -d "$root/apps/web/node_modules" ]]; then
    echo 'apps/web/node_modules is missing; run: pnpm --dir apps/web install --frozen-lockfile' >&2
    exit 1
  fi
fi

if [[ "$web_unit" == "1" ]]; then
  pnpm --dir apps/web test:unit
elif [[ ${#web_related[@]} -gt 0 ]]; then
  web_relative=()
  for path in "${web_related[@]}"; do
    web_relative+=("${path#apps/web/}")
  done
  pnpm --dir apps/web exec vitest related --run --passWithNoTests "${web_relative[@]}"
fi
