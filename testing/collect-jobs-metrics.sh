#!/bin/bash
# collect-jobs-metrics.sh — Log Canvas delayed_jobs queue depth, age, and
# throughput every 5s to a CSV for jobs-tier autoscaling analysis.
#
# Usage (run on SUT, in parallel with run-load-test.sh on load gen):
#   bash testing/collect-jobs-metrics.sh <output.csv>
#
# CSV schema:
#   timestamp                   — ISO8601 UTC
#   pending                     — jobs queued, not yet picked up by a worker
#   running                     — jobs currently being processed (locked_at NOT NULL)
#   failed                      — jobs in failed state
#   oldest_pending_age_sec      — age of oldest queued job (latency-to-start proxy)
#   total_processed_cumulative  — pg_stat_user_tables.n_tup_del for delayed_jobs
#                                 (delayed_job deletes rows on success). Use diff
#                                 between consecutive rows × (60/INTERVAL) to get
#                                 jobs-per-minute throughput in post-processing.
#
# Stop with Ctrl+C; the CSV stays valid (one row per scrape).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

OUTPUT_FILE="${1:-}"
INTERVAL_SECONDS="${JOBS_POLL_INTERVAL:-5}"
NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"
DB_NAME="${POSTGRES_DB:-canvas_production}"
DB_USER="${POSTGRES_USER:-canvas}"

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Usage: bash testing/collect-jobs-metrics.sh <output.csv>"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "timestamp,pending,running,failed,oldest_pending_age_sec,total_processed_cumulative" > "$OUTPUT_FILE"

# Single SQL round-trip per scrape. COALESCE on min(run_at) handles empty queue
# (returns 0 instead of NULL). n_tup_del is the cumulative count of deleted
# rows since stats reset — delayed_job removes rows on success, so this is a
# monotonic counter we can diff post-hoc to get throughput.
SQL="SELECT
  count(*) FILTER (WHERE locked_at IS NULL AND failed_at IS NULL) AS pending,
  count(*) FILTER (WHERE locked_at IS NOT NULL AND failed_at IS NULL) AS running,
  count(*) FILTER (WHERE failed_at IS NOT NULL) AS failed,
  COALESCE(EXTRACT(EPOCH FROM (now() - min(run_at)
    FILTER (WHERE locked_at IS NULL AND failed_at IS NULL AND run_at <= now()))), 0)::int AS oldest_age,
  COALESCE((SELECT n_tup_del FROM pg_stat_user_tables WHERE relname='delayed_jobs'), 0) AS processed
FROM delayed_jobs;"

echo "Polling delayed_jobs every ${INTERVAL_SECONDS}s -> $OUTPUT_FILE"
echo "Stop with Ctrl+C."

while true; do
  ts="$(date -Is)"

  row="$(kubectl exec -n "$NAMESPACE" deployment/postgres -- \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A -F ',' -c "$SQL" 2>/dev/null | head -1 || true)"
  row="${row:-0,0,0,0,0}"

  echo "${ts},${row}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
