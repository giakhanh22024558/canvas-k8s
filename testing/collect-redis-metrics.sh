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

  # CPU/memory from kubectl top. `timeout 4` caps the scrape below the 5s
  # interval so a hung kubectl call cannot stall the loop.
  top_line="$(timeout 4 kubectl top pod -n "$NAMESPACE" -l app=redis --no-headers 2>/dev/null | head -1 || true)"
  cpu="$(echo "$top_line" | awk '{print $2}' | sed 's/m$//')"
  # Empty (not 0) when kubectl top failed — chart pipeline reads it as NaN.
  cpu="${cpu:-}"

  # Single round-trip: pull all needed sections at once. INFO without args
  # returns everything; we'll grep the keys we care about. -c flag from kubectl
  # exec into the container's redis-cli.
  info_dump="$(timeout 4 kubectl exec -n "$NAMESPACE" deployment/redis -- \
    redis-cli INFO 2>/dev/null || true)"

  if [[ -z "$info_dump" ]]; then
    # Redis unreachable — emit EMPTY fields, not zeros. A row of zeros here
    # fabricates a "0 ops/sec, 0 hits, 0 misses" sample that is indistinguish-
    # able from a real idle Redis; empty cells render as a gap (the truth).
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

  # info_dump is non-empty here (the unreachable case `continue`d above), so
  # these are real readings. Individual keys missing from a valid INFO dump
  # fall back to empty (NaN in the chart) rather than a fabricated 0.
  used_mb=$([[ -n "${used_bytes:-}" ]] && echo $(( used_bytes / 1048576 )) || echo "")
  max_mb=$([[ -n "${max_bytes:-}" ]] && echo $(( max_bytes / 1048576 )) || echo "")

  echo "${ts},${cpu},${used_mb},${max_mb},${conn:-},${blocked:-},${ops:-},${hits:-},${misses:-},${evicted:-}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
