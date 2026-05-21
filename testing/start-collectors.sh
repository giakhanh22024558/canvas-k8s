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

# Auto-clean stale state on every start. Previously this script refused to
# start whenever the PID file existed, on the assumption that a prior batch
# was already running. That assumption is brittle: if the prior test was
# interrupted (Ctrl-C before the trap in run-load-test.sh installed, kill -9,
# host reboot, etc.), some collectors may have died while others survived,
# leaving a half-stale PID file. The old `pgrep -F` check returns success
# if ANY listed PID is still alive — so 1 surviving zombie was enough to
# block every subsequent run, silently losing the on-SUT CSVs for the test.
#
# New behaviour: if the file exists, iterate listed PIDs, kill any still
# alive, remove the file, then proceed with a fresh start. This makes the
# script idempotent and tolerant of any prior crash state. Belt-and-braces
# with run-load-test.sh's cleanup trap.
if [[ -f "$PID_FILE" ]]; then
  echo "Pre-existing PID file at $PID_FILE — auto-cleaning stale state ..."
  while read -r stale_pid; do
    [[ -z "$stale_pid" ]] && continue
    if kill -0 "$stale_pid" 2>/dev/null; then
      kill "$stale_pid" 2>/dev/null && echo "  killed stale pid=$stale_pid"
    fi
  done < "$PID_FILE"
  rm -f "$PID_FILE"
  echo "Stale state cleaned. Proceeding with fresh collector batch."
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
