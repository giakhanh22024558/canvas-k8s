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
  count(*) FILTER (WHERE wait_event_type IN ('Lock','LWLock','BufferPin')
                     AND state = 'active'),
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
  # `timeout 4` caps the scrape below the 5s interval — a hung kubectl call
  # on a CPU-saturated node must not stall the loop.
  top_line="$(timeout 4 kubectl top pod -n "$NAMESPACE" -l app=postgres --no-headers 2>/dev/null | head -1 || true)"
  cpu="$(echo "$top_line" | awk '{print $2}' | sed 's/m$//')"
  mem="$(echo "$top_line" | awk '{print $3}' | sed 's/Mi$//')"
  # Empty (not 0) when kubectl top failed — distinguishes "metrics scrape
  # failed" from a genuine near-idle reading. Chart pipeline reads empty as NaN.
  cpu="${cpu:-}"
  mem="${mem:-}"

  # 8-column row from SQL above
  pg_row="$(timeout 4 kubectl exec -n "$NAMESPACE" deployment/postgres -- \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A -F ',' -c "$SQL" 2>/dev/null | head -1 || true)"
  # 8 EMPTY fields (7 commas) when the SQL round-trip failed — previously this
  # backfilled 0,0,0,0,0,100,0,0 which fabricated a "0 active conns, 100% cache
  # hit" sample indistinguishable from a real idle reading.
  pg_row="${pg_row:-,,,,,,,}"

  echo "${ts},${cpu},${mem},${pg_row}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
