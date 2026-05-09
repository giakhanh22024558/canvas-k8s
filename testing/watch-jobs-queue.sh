#!/bin/bash
# watch-jobs-queue.sh — Real-time inspector for the Canvas delayed_jobs table.
# Run on the SUT in a separate terminal during a load test to confirm whether
# submissions actually create background jobs (the 5s collect-jobs-metrics
# poller might miss short-lived jobs between samples).
#
# Usage:
#   bash testing/watch-jobs-queue.sh           # 1s polling, 10 most recent jobs
#   INTERVAL=2 LIMIT=20 bash testing/watch-jobs-queue.sh
#
# Stop with Ctrl+C.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"
DB_NAME="${POSTGRES_DB:-canvas_production}"
DB_USER="${POSTGRES_USER:-canvas}"
INTERVAL="${INTERVAL:-1}"
LIMIT="${LIMIT:-10}"

# Two queries each tick:
#   (a) Aggregate stats: total / pending / running / failed / oldest age
#   (b) Most recent N rows: id, run_at, locked_at, attempts, handler_class
SQL_STATS="SELECT
  count(*)                                                              AS total_rows,
  count(*) FILTER (WHERE locked_at IS NULL AND failed_at IS NULL)       AS pending,
  count(*) FILTER (WHERE locked_at IS NOT NULL AND failed_at IS NULL)   AS running,
  count(*) FILTER (WHERE failed_at IS NOT NULL)                         AS failed,
  COALESCE(EXTRACT(EPOCH FROM (now() - min(run_at))) FILTER
    (WHERE locked_at IS NULL AND failed_at IS NULL AND run_at <= now()), 0)::int AS oldest_age_sec,
  COALESCE((SELECT n_tup_ins FROM pg_stat_user_tables WHERE relname='delayed_jobs'), 0) AS jobs_ever_inserted,
  COALESCE((SELECT n_tup_del FROM pg_stat_user_tables WHERE relname='delayed_jobs'), 0) AS jobs_ever_deleted
FROM delayed_jobs;"

SQL_RECENT="SELECT
  id,
  to_char(run_at, 'HH24:MI:SS')                       AS run_at,
  CASE WHEN locked_at IS NULL THEN '-' ELSE 'YES'  END AS locked,
  attempts,
  COALESCE(substring(handler from 'object: !ruby/object:([^\\\\n]+)'), '?') AS handler_class
FROM delayed_jobs
ORDER BY id DESC
LIMIT ${LIMIT};"

echo "Watching delayed_jobs every ${INTERVAL}s (Ctrl+C to stop)"
echo ""

while true; do
  clear
  echo "===== $(date -Is) ====="
  echo ""

  echo "Aggregate stats:"
  kubectl exec -n "$NAMESPACE" deployment/postgres -- \
    psql -U "$DB_USER" -d "$DB_NAME" -c "$SQL_STATS" 2>/dev/null \
    || echo "  (query failed)"

  echo ""
  echo "Recent jobs (last $LIMIT):"
  kubectl exec -n "$NAMESPACE" deployment/postgres -- \
    psql -U "$DB_USER" -d "$DB_NAME" -c "$SQL_RECENT" 2>/dev/null \
    || echo "  (query failed)"

  sleep "$INTERVAL"
done
