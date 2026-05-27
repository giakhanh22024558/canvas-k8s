#!/bin/bash
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

SQL="SELECT
  count(*) FILTER (WHERE locked_at IS NULL AND failed_at IS NULL) AS pending,
  count(*) FILTER (WHERE locked_at IS NOT NULL AND failed_at IS NULL) AS running,
  count(*) FILTER (WHERE failed_at IS NOT NULL) AS failed,
  COALESCE(EXTRACT(EPOCH FROM (now() - min(run_at)
    FILTER (WHERE locked_at IS NULL AND failed_at IS NULL
            AND run_at <= now()
            AND run_at >= now() - interval '1 hour'))), 0)::int AS oldest_age,
  COALESCE((SELECT n_tup_del FROM pg_stat_user_tables WHERE relname='delayed_jobs'), 0) AS processed
FROM delayed_jobs;"

echo "Polling delayed_jobs every ${INTERVAL_SECONDS}s -> $OUTPUT_FILE"
echo "Stop with Ctrl+C."

while true; do
  ts="$(date -Is)"

  row="$(timeout 4 kubectl exec -n "$NAMESPACE" deployment/postgres -- \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A -F ',' -c "$SQL" 2>/dev/null | head -1 || true)"

  if [[ -z "$row" ]]; then
    echo "${ts},,,,," >> "$OUTPUT_FILE"
  else
    echo "${ts},${row}" >> "$OUTPUT_FILE"
  fi
  sleep "$INTERVAL_SECONDS"
done
