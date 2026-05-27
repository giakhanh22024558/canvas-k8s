#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
TEST_ID="${TEST_ID:-}"

echo "===== finalize-run.sh ====="

bash "$SCRIPT_DIR/stop-collectors.sh" || true

if [[ -z "$TEST_ID" ]]; then
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

TEST_ID="$TEST_ID" bash "$SCRIPT_DIR/publish-results.sh"

echo "===== finalize-run done ====="
