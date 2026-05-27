#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
STEP="${STEP:-15s}"
TEST_ID="${TEST_ID:-}"

LOADGEN_SSH_HOST="${LOADGEN_SSH_HOST:-}"
LOADGEN_RESULTS_DIR="${LOADGEN_RESULTS_DIR:-/home/ubuntu/canvas-k8s/testing/results}"
LOADGEN_SSH_KEY="${LOADGEN_SSH_KEY:-}"

if [[ -n "$LOADGEN_SSH_HOST" ]]; then
  SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
  if [[ -n "$LOADGEN_SSH_KEY" ]]; then
    SSH_OPTS="$SSH_OPTS -i $LOADGEN_SSH_KEY"
  fi

  echo "Syncing run data from load gen ($LOADGEN_SSH_HOST) ..."
  mkdir -p "$RESULTS_DIR"

  if [[ -n "$TEST_ID" ]]; then
    rsync -az --info=stats1 -e "ssh $SSH_OPTS" \
      "$LOADGEN_SSH_HOST:$LOADGEN_RESULTS_DIR/$TEST_ID/" \
      "$RESULTS_DIR/$TEST_ID/" \
      || { echo "ERROR: rsync of $TEST_ID failed"; exit 1; }
  else
    rsync -az --update --info=stats1 -e "ssh $SSH_OPTS" \
      --include='canvas-*/' --include='canvas-*/**' --exclude='*' \
      "$LOADGEN_SSH_HOST:$LOADGEN_RESULTS_DIR/" \
      "$RESULTS_DIR/" \
      || { echo "ERROR: rsync from load gen failed"; exit 1; }
  fi
  echo "Sync complete."
fi

if [[ -z "$TEST_ID" ]]; then
  RUN_DIR="$(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -type d \
              -regextype posix-extended -regex '.*-[0-9]{8}-[0-9]{6}$' \
              | sort | tail -n 1)"
  TEST_ID="$(basename "$RUN_DIR")"
else
  RUN_DIR="$RESULTS_DIR/$TEST_ID"
fi

if [[ -z "${RUN_DIR:-}" || ! -d "$RUN_DIR" ]]; then
  echo "Could not find a load test run directory. Pass TEST_ID or run ./testing/run-load-test.sh first."
  if [[ -n "$LOADGEN_SSH_HOST" ]]; then
    echo "Hint: rsync ran from $LOADGEN_SSH_HOST:$LOADGEN_RESULTS_DIR — verify path exists there."
  fi
  exit 1
fi

PROM_QUERY_URL="$(prometheus_query_url)"

echo "Publishing results for test: $TEST_ID"
echo "Prometheus query URL: $PROM_QUERY_URL"

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

cd "$REPO_ROOT"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Pulling latest changes on branch $BRANCH ..."
git pull origin "$BRANCH" --rebase || echo "WARNING: git pull failed. Continuing with local code."

echo "Cleaning stale chart files in $RUN_DIR ..."
rm -f "$RUN_DIR"/*.png
rm -f "$RUN_DIR"/summary_comparison.csv

echo "Generating charts..."

METADATA_TESTID="$(grep '^test_id=' "$RUN_DIR/metadata.env" 2>/dev/null | cut -d= -f2 || true)"
PROM_TESTID="${METADATA_TESTID:-$TEST_ID}"

WEB_MEMORY_LIMIT="${WEB_MEMORY_LIMIT:-}"
JOBS_MEMORY_LIMIT="${JOBS_MEMORY_LIMIT:-}"

"$PYTHON" "$SCRIPT_DIR/charts/plot_prometheus.py" \
  --testid "$PROM_TESTID" \
  --runs-dir "$RESULTS_DIR" \
  --run-dir "$RUN_DIR" \
  --prometheus-url "$PROM_QUERY_URL" \
  --output-dir "$RUN_DIR" \
  --step "$STEP" \
  --web-memory-limit "$WEB_MEMORY_LIMIT" \
  --jobs-memory-limit "$JOBS_MEMORY_LIMIT"

echo "Charts generated in $RUN_DIR"

git add -A "testing/results/$TEST_ID/"

if git diff --cached --quiet; then
  echo "No new result files to commit for $TEST_ID."
  exit 0
fi

git commit -m "Add test results for $TEST_ID"

echo "Pushing to origin/$BRANCH ..."
git push origin "$BRANCH"

echo "Done. Results for $TEST_ID published to origin/$BRANCH."
