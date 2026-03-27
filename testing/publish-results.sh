#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
CHARTS_DIR="${CHARTS_DIR:-$SCRIPT_DIR/charts/output}"
RESULTS_REPO_URL="${RESULTS_REPO_URL:-https://github.com/giakhanh22024558/canvas-k8s-results.git}"
RESULTS_REPO_DIR="${RESULTS_REPO_DIR:-$SCRIPT_DIR/results-publish-repo}"
TEST_ID="${TEST_ID:-}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required but not installed."
  exit 1
fi

if [[ -n "$TEST_ID" ]]; then
  RUN_DIR="$RESULTS_DIR/$TEST_ID"
else
  RUN_DIR="$(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
  TEST_ID="$(basename "$RUN_DIR")"
fi

if [[ -z "${RUN_DIR:-}" || ! -d "$RUN_DIR" ]]; then
  echo "Could not find a load test run directory. Run ./testing/run-load-test.sh first or pass TEST_ID."
  exit 1
fi

if [[ ! -d "$CHARTS_DIR" ]]; then
  echo "Charts directory not found at $CHARTS_DIR. Generate charts first."
  exit 1
fi

if [[ ! -d "$RESULTS_REPO_DIR/.git" ]]; then
  echo "Cloning results repo into $RESULTS_REPO_DIR"
  git clone "$RESULTS_REPO_URL" "$RESULTS_REPO_DIR"
else
  echo "Updating existing results repo in $RESULTS_REPO_DIR"
  git -C "$RESULTS_REPO_DIR" pull --ff-only
fi

TARGET_DIR="$RESULTS_REPO_DIR/runs/$TEST_ID"
mkdir -p "$TARGET_DIR"

rm -rf "$TARGET_DIR/run" "$TARGET_DIR/charts"
cp -R "$RUN_DIR" "$TARGET_DIR/run"
cp -R "$CHARTS_DIR" "$TARGET_DIR/charts"

cat > "$TARGET_DIR/publish-info.txt" <<EOF
test_id=$TEST_ID
published_at=$(date -Is)
base_url=${BASE_URL:-http://canvas.io.vn}
prom_url=${PROM_URL:-http://127.0.0.1:30090/api/v1/write}
source_run_dir=$RUN_DIR
source_charts_dir=$CHARTS_DIR
EOF

git -C "$RESULTS_REPO_DIR" add "runs/$TEST_ID"

if git -C "$RESULTS_REPO_DIR" diff --cached --quiet; then
  echo "No result changes to commit for $TEST_ID"
  exit 0
fi

git -C "$RESULTS_REPO_DIR" commit -m "Add test results for $TEST_ID"
git -C "$RESULTS_REPO_DIR" push

echo "Published results for $TEST_ID to $RESULTS_REPO_URL"
