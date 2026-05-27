#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_DIR="/tmp/collectors-$(date +%Y%m%d-%H%M%S)"
OUTPUT_DIR="${OUTPUT_DIR:-$DEFAULT_DIR}"
PID_FILE="${COLLECTORS_PID_FILE:-/tmp/collectors.pids}"
LOG_DIR="${COLLECTORS_LOG_DIR:-/tmp}"

mkdir -p "$OUTPUT_DIR"

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
start_one k8s-snaps  "$OUTPUT_DIR/k8s-snapshots.csv"   "collect-k8s-snapshots.sh"

echo ""
echo "Logs: $LOG_DIR/c-{jobs,postgres,redis,k8s-snaps}.log"
echo "Stop with: bash testing/stop-collectors.sh"
echo "PID file: $PID_FILE"
echo "OUTPUT_DIR=$OUTPUT_DIR"
