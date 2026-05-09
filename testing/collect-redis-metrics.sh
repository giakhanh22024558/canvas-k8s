#!/bin/bash
# collect-redis-metrics.sh — Log Redis CPU/memory, hit ratio, ops/sec, and
# eviction counters every 5s for "cache is not the bottleneck" validation.
#
# Usage (run on SUT, in parallel with run-load-test.sh on load gen):
#   bash testing/collect-redis-metrics.sh <output.csv>
#
# CSV schema:
#   timestamp                       — ISO8601 UTC
#   redis_cpu_millicores            — kubectl top pod
#   redis_memory_used_mb            — INFO memory.used_memory / 1024^2
#   redis_memory_max_mb             — INFO memory.maxmemory / 1024^2 (0 = unbounded)
#   connected_clients               — INFO clients.connected_clients
#   blocked_clients                 — INFO clients.blocked_clients
#   ops_per_sec                     — INFO stats.instantaneous_ops_per_sec
#   keyspace_hits_cumulative        — monotonic counter; diff post-hoc for hit rate
#   keyspace_misses_cumulative      — monotonic counter
#   evicted_keys_cumulative         — monotonic counter; nonzero = maxmemory hit
#
# Stop with Ctrl+C; the CSV stays valid (one row per scrape).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

OUTPUT_FILE="${1:-}"
INTERVAL_SECONDS="${REDIS_POLL_INTERVAL:-5}"
NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Usage: bash testing/collect-redis-metrics.sh <output.csv>"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "timestamp,redis_cpu_millicores,redis_memory_used_mb,redis_memory_max_mb,connected_clients,blocked_clients,ops_per_sec,keyspace_hits_cumulative,keyspace_misses_cumulative,evicted_keys_cumulative" > "$OUTPUT_FILE"

# Helper: parse one numeric field from `redis-cli INFO` output. Handles the
# CRLF line endings Redis uses.
parse_info() {
  local section="$1" key="$2"
  awk -F: -v k="$key" '$1 == k { gsub(/\r/,"",$2); print $2; exit }' <<<"$section"
}

echo "Polling Redis every ${INTERVAL_SECONDS}s -> $OUTPUT_FILE"
echo "Stop with Ctrl+C."

while true; do
  ts="$(date -Is)"

  # CPU/memory from kubectl top
  top_line="$(kubectl top pod -n "$NAMESPACE" -l app=redis --no-headers 2>/dev/null | head -1 || true)"
  cpu="$(echo "$top_line" | awk '{print $2}' | sed 's/m$//')"
  cpu="${cpu:-0}"

  # Single round-trip: pull all needed sections at once. INFO without args
  # returns everything; we'll grep the keys we care about. -c flag from kubectl
  # exec into the container's redis-cli.
  info_dump="$(kubectl exec -n "$NAMESPACE" deployment/redis -- \
    redis-cli INFO 2>/dev/null || true)"

  if [[ -z "$info_dump" ]]; then
    # Redis unreachable — write a row of zeros so the timeline is continuous
    echo "${ts},${cpu},0,0,0,0,0,0,0,0" >> "$OUTPUT_FILE"
    sleep "$INTERVAL_SECONDS"
    continue
  fi

  used_bytes="$(parse_info "$info_dump" used_memory)"
  max_bytes="$(parse_info "$info_dump" maxmemory)"
  conn="$(parse_info "$info_dump" connected_clients)"
  blocked="$(parse_info "$info_dump" blocked_clients)"
  ops="$(parse_info "$info_dump" instantaneous_ops_per_sec)"
  hits="$(parse_info "$info_dump" keyspace_hits)"
  misses="$(parse_info "$info_dump" keyspace_misses)"
  evicted="$(parse_info "$info_dump" evicted_keys)"

  used_mb=$(( ${used_bytes:-0} / 1048576 ))
  max_mb=$(( ${max_bytes:-0} / 1048576 ))

  echo "${ts},${cpu},${used_mb},${max_mb},${conn:-0},${blocked:-0},${ops:-0},${hits:-0},${misses:-0},${evicted:-0}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
