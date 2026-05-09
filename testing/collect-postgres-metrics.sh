#!/bin/bash
# collect-postgres-metrics.sh — Log Postgres CPU/memory + connection state
# every 5s to a CSV for bottleneck analysis.
#
# Usage:
#   bash testing/collect-postgres-metrics.sh <output.csv>
#
# Run in parallel with run-load-test.sh to capture the same time window.
# Stop with Ctrl+C; the CSV stays valid (one row per scrape).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

OUTPUT_FILE="${1:-}"
INTERVAL_SECONDS="${POSTGRES_SNAPSHOT_INTERVAL:-5}"
NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"
DB_NAME="${POSTGRES_DB:-canvas_production}"
DB_USER="${POSTGRES_USER:-canvas}"

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Usage: bash testing/collect-postgres-metrics.sh <output.csv>"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "timestamp,postgres_cpu_millicores,postgres_memory_mib,active_conns,idle_conns,idle_in_tx_conns,waiting_on_locks,slow_queries_over_1s,max_connections,cache_hit_ratio_percent,xact_commit_cumulative" > "$OUTPUT_FILE"

# Single quoted SQL — one row, comma-separated. Combines pg_stat_activity
# state counts with max_connections setting, global cache hit ratio, and
# cumulative committed transactions for our database.
SQL="SELECT
  count(*) FILTER (WHERE state = 'active'),
  count(*) FILTER (WHERE state = 'idle'),
  count(*) FILTER (WHERE state = 'idle in transaction'),
  count(*) FILTER (WHERE wait_event_type IS NOT NULL),
  count(*) FILTER (WHERE state = 'active' AND now() - query_start > interval '1 second'),
  (SELECT current_setting('max_connections')::int),
  (SELECT round(100.0 * sum(blks_hit)::numeric / nullif(sum(blks_hit + blks_read), 0), 2)
   FROM pg_stat_database),
  (SELECT COALESCE(xact_commit + xact_rollback, 0)
   FROM pg_stat_database WHERE datname = '${DB_NAME}')
FROM pg_stat_activity
WHERE datname = '${DB_NAME}';"

while true; do
  ts="$(date -Is)"

  # CPU/memory from kubectl top — output: NAME CPU(cores) MEMORY(bytes)
  # e.g. "postgres-7fc854799f-jzdr7   234m   512Mi"
  top_line="$(kubectl top pod -n "$NAMESPACE" -l app=postgres --no-headers 2>/dev/null | head -1 || true)"
  cpu="$(echo "$top_line" | awk '{print $2}' | sed 's/m$//')"
  mem="$(echo "$top_line" | awk '{print $3}' | sed 's/Mi$//')"
  cpu="${cpu:-0}"
  mem="${mem:-0}"

  # 8-column row from SQL above
  pg_row="$(kubectl exec -n "$NAMESPACE" deployment/postgres -- \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A -F ',' -c "$SQL" 2>/dev/null | head -1 || true)"
  pg_row="${pg_row:-0,0,0,0,0,100,0,0}"

  echo "${ts},${cpu},${mem},${pg_row}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
