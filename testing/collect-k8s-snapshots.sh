#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

OUTPUT_FILE="${1:-}"
INTERVAL_SECONDS="${SNAPSHOT_INTERVAL_SECONDS:-5}"
NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Usage: ./testing/collect-k8s-snapshots.sh <output-file>"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "timestamp,web_ready_replicas,web_available_replicas,web_spec_replicas,jobs_ready_replicas,jobs_available_replicas,jobs_spec_replicas,web_hpa_current_replicas,web_hpa_desired_replicas,jobs_hpa_current_replicas,jobs_hpa_desired_replicas,web_restart_total,jobs_restart_total" > "$OUTPUT_FILE"

jsonpath_value() {
  local kind="$1"
  local name="$2"
  local path="$3"
  kubectl get "$kind" "$name" -n "$NAMESPACE" -o "jsonpath=${path}" 2>/dev/null || true
}

restart_sum() {
  local label="$1"
  kubectl get pods -n "$NAMESPACE" -l "app=$label" \
    -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}' 2>/dev/null \
    | awk '{sum += $1} END {print sum + 0}'
}

while true; do
  timestamp="$(date -Is)"
  web_ready="$(jsonpath_value deployment canvas-web '{.status.readyReplicas}')"
  web_available="$(jsonpath_value deployment canvas-web '{.status.availableReplicas}')"
  web_spec="$(jsonpath_value deployment canvas-web '{.spec.replicas}')"
  jobs_ready="$(jsonpath_value deployment canvas-jobs '{.status.readyReplicas}')"
  jobs_available="$(jsonpath_value deployment canvas-jobs '{.status.availableReplicas}')"
  jobs_spec="$(jsonpath_value deployment canvas-jobs '{.spec.replicas}')"

  web_hpa_current="$(jsonpath_value hpa canvas-web '{.status.currentReplicas}')"
  web_hpa_desired="$(jsonpath_value hpa canvas-web '{.status.desiredReplicas}')"
  jobs_hpa_current="$(jsonpath_value hpa canvas-jobs '{.status.currentReplicas}')"
  jobs_hpa_desired="$(jsonpath_value hpa canvas-jobs '{.status.desiredReplicas}')"

  web_restarts="$(restart_sum canvas-web)"
  jobs_restarts="$(restart_sum canvas-jobs)"

  echo "${timestamp},${web_ready:-0},${web_available:-0},${web_spec:-0},${jobs_ready:-0},${jobs_available:-0},${jobs_spec:-0},${web_hpa_current:-0},${web_hpa_desired:-0},${jobs_hpa_current:-0},${jobs_hpa_desired:-0},${web_restarts:-0},${jobs_restarts:-0}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
