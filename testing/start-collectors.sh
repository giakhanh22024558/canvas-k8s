#!/bin/bash
# start-collectors.sh — Launch all three on-SUT collectors (jobs queue,
# Postgres health, Redis health) in the background and write their CSVs
# into a target directory (default: a tmp folder keyed by current time).
# Each collector PID is recorded so stop-collectors.sh can kill them.
#
# Usage:
#   bash testing/start-collectors.sh                     # writes to /tmp/collectors-<ts>
#   OUTPUT_DIR=testing/results/canvas-XXXX bash testing/start-collectors.sh
#
# After the load test on the load gen finishes:
#   bash testing/stop-collectors.sh
# Then copy the three CSVs into the per-run folder (or use OUTPUT_DIR
# pointing directly at the run folder so no copy is needed).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_DIR="/tmp/collectors-$(date +%Y%m%d-%H%M%S)"
OUTPUT_DIR="${OUTPUT_DIR:-$DEFAULT_DIR}"
PID_FILE="${COLLECTORS_PID_FILE:-/tmp/collectors.pids}"
LOG_DIR="${COLLECTORS_LOG_DIR:-/tmp}"

mkdir -p "$OUTPUT_DIR"

# Refuse to start if a previous batch is still running. stop-collectors.sh
# clears the PID file after killing, so this trips only on accidental
# double-start.
if [[ -f "$PID_FILE" ]]; then
  if pgrep -F "$PID_FILE" >/dev/null 2>&1; then
    echo "Collectors already running (PID file: $PID_FILE)."
    echo "Run 'bash testing/stop-collectors.sh' first."
    exit 1
  else
    rm -f "$PID_FILE"
  fi
fi

start_one() {
  local name="$1" csv="$2" script="$3"
  nohup bash "$SCRIPT_DIR/${script}" "$csv" \
    > "$LOG_DIR/c-${name}.log" 2>&1 &
  local pid=$!
  echo "$pid" >> "$PID_FILE"
  printf "  %-12s pid=%-7d csv=%s\n" "$name" "$pid" "$csv"
}

echo "Starting collectors → $OUTPUT_DIR"
start_one jobs       "$OUTPUT_DIR/jobs-queue.csv"      "collect-jobs-metrics.sh"
start_one postgres   "$OUTPUT_DIR/postgres-health.csv" "collect-postgres-metrics.sh"
start_one redis      "$OUTPUT_DIR/redis-health.csv"    "collect-redis-metrics.sh"
# k8s-snapshots is the canonical source for replica counts, restart counters,
# HPA desired/current state and scale-latency derivations. The load-gen has no
# kubectl so run-load-test.sh skips its built-in snapshot; the SUT must run it
# in this collector batch instead. Without it, max_web_restart_total stays at
# 0 in the summary CSV even when pods are actively OOMKill-looping (Stage 1).
start_one k8s-snaps  "$OUTPUT_DIR/k8s-snapshots.csv"   "collect-k8s-snapshots.sh"

echo ""
echo "Logs: $LOG_DIR/c-{jobs,postgres,redis,k8s-snaps}.log"
echo "Stop with: bash testing/stop-collectors.sh"
echo "PID file: $PID_FILE"
echo "OUTPUT_DIR=$OUTPUT_DIR"
