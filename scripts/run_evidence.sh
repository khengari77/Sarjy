#!/usr/bin/env bash
# Run the issue #113 acceptance evidence suite at one exact revision.
#
#   bash scripts/run_evidence.sh                 # full visual/interaction suite
#   bash scripts/run_evidence.sh --grep "layout" # one evidence slice
#   SARJY_RESOURCE_PROFILE=1 SARJY_RESOURCE_PROFILE_SECONDS=300 \
#     bash scripts/run_evidence.sh --grep "resource profile"
#
# Artifacts are written to the ignored test-results/evidence/<sha>/ directory:
# report.json, index.md, summary.json, screens/*.png and measured JSON files.
# A red row or a skipped row in index.md is not acceptance.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHA="${SARJY_EVIDENCE_SHA:-$(git -C "$ROOT" rev-parse --verify HEAD)}"
OUT="${SARJY_EVIDENCE_DIR:-$ROOT/test-results/evidence/$SHA}"

mkdir -p "$OUT"
export SARJY_EVIDENCE_SHA="$SHA"
export SARJY_EVIDENCE_DIR="$OUT"
export SARJY_EVIDENCE_COMMAND="SARJY_RESOURCE_PROFILE=${SARJY_RESOURCE_PROFILE:-0} bash scripts/run_evidence.sh $*"
# Force Playwright to start its own servers instead of reusing a neighbour's.
export CI="${CI:-1}"

echo "Evidence SHA: $SHA"
echo "Artifacts:    $OUT"

cd "$ROOT"
set +e
flock /tmp/sarjy-browser-tests.lock pnpm --dir apps/web test:evidence "$@"
STATUS=$?
set -e
node apps/web/scripts/evidence-index.mjs "$OUT"
exit "$STATUS"
