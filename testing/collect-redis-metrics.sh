#!/bin/bash
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

parse_info() {
  local section="$1" key="$2"
  awk -F: -v k="$key" '$1 == k { gsub(/\r/,"",$2); print $2; exit }' <<<"$section"
}

echo "Polling Redis every ${INTERVAL_SECONDS}s -> $OUTPUT_FILE"
echo "Stop with Ctrl+C."

while true; do
  ts="$(date -Is)"

  top_line="$(timeout 4 kubectl top pod -n "$NAMESPACE" -l app=redis --no-headers 2>/dev/null | head -1 || true)"
  cpu="$(echo "$top_line" | awk '{print $2}' | sed 's/m$//')"
  cpu="${cpu:-}"

  info_dump="$(timeout 4 kubectl exec -n "$NAMESPACE" deployment/redis -- \
    redis-cli INFO 2>/dev/null || true)"

  if [[ -z "$info_dump" ]]; then
    echo "${ts},${cpu},,,,,,,," >> "$OUTPUT_FILE"
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

  used_mb=$([[ -n "${used_bytes:-}" ]] && echo $(( used_bytes / 1048576 )) || echo "")
  max_mb=$([[ -n "${max_bytes:-}" ]] && echo $(( max_bytes / 1048576 )) || echo "")

  echo "${ts},${cpu},${used_mb},${max_mb},${conn:-},${blocked:-},${ops:-},${hits:-},${misses:-},${evicted:-}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
