#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
STEP="${STEP:-15s}"
TEST_ID="${TEST_ID:-}"

if [[ -z "$TEST_ID" ]]; then
  # Only consider timestamped run folders (canvas-YYYYMMDD-HHMMSS).
  # Plain `sort` works here because the date+time format is lexicographically
  # ordered — the most recent run always sorts last.
  # Non-timestamped folders like grafana-stress-check are excluded.
  RUN_DIR="$(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -type d -name 'canvas-*' | sort | tail -n 1)"
  TEST_ID="$(basename "$RUN_DIR")"
else
  RUN_DIR="$RESULTS_DIR/$TEST_ID"
fi

if [[ -z "${RUN_DIR:-}" || ! -d "$RUN_DIR" ]]; then
  echo "Could not find a load test run directory. Pass TEST_ID or run ./testing/run-load-test.sh first."
  exit 1
fi

PROM_QUERY_URL="$(prometheus_query_url)"

echo "Publishing results for test: $TEST_ID"
echo "Prometheus query URL: $PROM_QUERY_URL"

# --- Find Python in venv or system ---
PYTHON=""
for candidate in "$REPO_ROOT/.venv/bin/python3" "$REPO_ROOT/.venv/bin/python" "$(command -v python3 2>/dev/null)" "$(command -v python 2>/dev/null)"; do
  if [[ -x "$candidate" ]]; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "ERROR: Python not found. Activate your venv first: source .venv/bin/activate"
  exit 1
fi

echo "Using Python: $PYTHON"

# --- Pull latest code BEFORE generating charts so plot fixes are applied ---
cd "$REPO_ROOT"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Pulling latest changes on branch $BRANCH ..."
git pull origin "$BRANCH" --rebase || echo "WARNING: git pull failed. Continuing with local code."

# Remove stale chart PNGs before regenerating so files that the new code no
# longer produces (e.g. hpa_cpu for baseline, comparison bar for single run)
# don't linger in the results directory and mislead readers.
echo "Cleaning stale chart files in $RUN_DIR ..."
rm -f "$RUN_DIR"/*.png

echo "Generating charts..."

"$PYTHON" "$SCRIPT_DIR/charts/plot_prometheus.py" \
  --testid "$TEST_ID" \
  --runs-dir "$RESULTS_DIR" \
  --prometheus-url "$PROM_QUERY_URL" \
  --output-dir "$RUN_DIR" \
  --step "$STEP"

echo "Charts generated in $RUN_DIR"

git add "testing/results/$TEST_ID/"

if git diff --cached --quiet; then
  echo "No new result files to commit for $TEST_ID."
  exit 0
fi

git commit -m "Add test results for $TEST_ID"

echo "Pushing to origin/$BRANCH ..."
git push origin "$BRANCH"

echo "Done. Results for $TEST_ID published to origin/$BRANCH."
