#!/bin/bash
# finalize-run.sh — Single-command post-test cleanup on the SUT.
#
# After k6 finishes on the load gen, this script:
#   1. Stops the on-SUT collector batch (jobs queue + Postgres + Redis +
#      k8s-snapshots) started by start-collectors.sh.
#   2. Identifies the latest /tmp/collectors-* dir and the latest run folder
#      under testing/results/canvas-* (or uses an explicit TEST_ID).
#   3. Copies the four collector CSVs into the run folder so the chart
#      pipeline picks them up.
#   4. Runs publish-results.sh which rsyncs raw k6 data from the load gen,
#      generates charts, and commits + pushes to origin.
#
# Usage:
#   bash testing/finalize-run.sh                          # latest run, latest collectors
#   TEST_ID=canvas-... bash testing/finalize-run.sh       # specific run
#
# Designed to be invoked by run-load-test.sh on the load gen via SSH so the
# whole start-collectors → k6 → stop+publish pipeline becomes a single
# command on the load gen.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
TEST_ID="${TEST_ID:-}"

echo "===== finalize-run.sh ====="

# 1. Stop collectors (silent if not running)
bash "$SCRIPT_DIR/stop-collectors.sh" || true

# 2. Resolve run folder
if [[ -z "$TEST_ID" ]]; then
  # Match any folder whose name ends in -YYYYMMDD-HHMMSS — covers both legacy
  # canvas-* runs and the new {EXPERIMENT_NAME}-{timestamp} convention.
  RUN_DIR="$(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -type d \
              -regextype posix-extended -regex '.*-[0-9]{8}-[0-9]{6}$' \
              | sort | tail -n 1)"
  if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no canvas-* run folder under $RESULTS_DIR — has the load gen pushed yet?"
    exit 1
  fi
  TEST_ID="$(basename "$RUN_DIR")"
else
  RUN_DIR="$RESULTS_DIR/$TEST_ID"
fi
echo "Run: $TEST_ID"

# 3. Resolve latest collector dir
LATEST_COLLECTORS="$(ls -td /tmp/collectors-* 2>/dev/null | head -1 || true)"
if [[ -z "$LATEST_COLLECTORS" ]]; then
  echo "WARN: no /tmp/collectors-* found — was start-collectors.sh run before the test?"
else
  echo "Collectors: $LATEST_COLLECTORS"
  mkdir -p "$RUN_DIR"
  for csv in jobs-queue.csv postgres-health.csv redis-health.csv k8s-snapshots.csv; do
    if [[ -f "$LATEST_COLLECTORS/$csv" ]]; then
      cp "$LATEST_COLLECTORS/$csv" "$RUN_DIR/"
      echo "  → folded $csv"
    else
      echo "  WARN: $csv not in collector dir"
    fi
  done
fi

# 4. Publish (rsync from LG + charts + git push)
TEST_ID="$TEST_ID" bash "$SCRIPT_DIR/publish-results.sh"

echo "===== finalize-run done ====="
