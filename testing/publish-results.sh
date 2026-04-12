#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
PROM_URL="${PROM_URL:-http://127.0.0.1:30090}"
STEP="${STEP:-15s}"
TEST_ID="${TEST_ID:-}"

if [[ -z "$TEST_ID" ]]; then
  RUN_DIR="$(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
  TEST_ID="$(basename "$RUN_DIR")"
else
  RUN_DIR="$RESULTS_DIR/$TEST_ID"
fi

if [[ -z "${RUN_DIR:-}" || ! -d "$RUN_DIR" ]]; then
  echo "Could not find a load test run directory. Pass TEST_ID or run ./testing/run-load-test.sh first."
  exit 1
fi

echo "Publishing results for test: $TEST_ID"

# --- Plot metrics ---
VENV_PYTHON=""
for candidate in "$REPO_ROOT/.venv/bin/python3" "$REPO_ROOT/.venv/bin/python" "python3" "python"; do
  if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then
    VENV_PYTHON="$candidate"
    break
  fi
done

if [[ -z "$VENV_PYTHON" ]]; then
  echo "WARNING: Python not found, skipping chart generation."
else
  echo "Generating charts with $VENV_PYTHON ..."
  "$VENV_PYTHON" "$SCRIPT_DIR/charts/plot_prometheus.py" \
    --testid "$TEST_ID" \
    --runs-dir "$RESULTS_DIR" \
    --prometheus-url "$PROM_URL" \
    --output-dir "$RUN_DIR" \
    --step "$STEP" \
    || echo "WARNING: Chart generation failed, continuing with publish."
fi

# --- Commit and push to current repo ---
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "Pulling latest changes on branch $BRANCH ..."
git pull origin "$BRANCH" --rebase || {
  echo "WARNING: git pull failed. Attempting push anyway."
}

git add "testing/results/$TEST_ID/"

if git diff --cached --quiet; then
  echo "No new result files to commit for $TEST_ID."
  exit 0
fi

git commit -m "Add test results for $TEST_ID"

echo "Pushing to origin/$BRANCH ..."
git push origin "$BRANCH"

echo "Done. Results for $TEST_ID published to origin/$BRANCH."
