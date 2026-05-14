#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

OUTPUT_FILE="${1:-}"
# 1-second sampling. This collector feeds compute_scale_events() in
# plot_prometheus.py, which derives HPA scale-out/scale-in latency from the
# gap between web_hpa_desired_replicas rising and web_ready_replicas catching
# up. At the previous 5 s interval a scale event that completes in ~6 s was
# only resolvable to "within one sampling bucket" — stage3-hpa-run01 reported
# 0.0 s and run02 reported 6–7 s for what is physically the same sub-10 s
# latency. 1 s sampling resolves it properly. Cost is modest: ~10 fast
# kubectl GETs per tick (each already wrapped in `timeout 4`), well within
# k3s API-server capacity on the m6a.2xlarge node, and a 38 min run yields a
# ~2300-row CSV (still tiny). Only this collector is sped up — the jobs/
# postgres/redis collectors keep their 5 s interval since their DB round-
# trips neither need nor warrant 1 s resolution.
INTERVAL_SECONDS="${SNAPSHOT_INTERVAL_SECONDS:-1}"
NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Usage: ./testing/collect-k8s-snapshots.sh <output-file>"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "timestamp,web_ready_replicas,web_available_replicas,web_spec_replicas,jobs_ready_replicas,jobs_available_replicas,jobs_spec_replicas,web_hpa_current_replicas,web_hpa_desired_replicas,jobs_hpa_current_replicas,jobs_hpa_desired_replicas,web_restart_total,jobs_restart_total" > "$OUTPUT_FILE"

# Distinguishes "kubectl call failed" from "field is genuinely absent/0".
# When kubectl exits 0 but prints nothing, the field is legitimately missing
# (e.g. .status.readyReplicas is omitted when it equals 0) → report 0. When
# kubectl itself fails (API throttle, timeout), report EMPTY so the chart
# pipeline reads NaN and renders a gap instead of a fabricated 0. `timeout 4`
# caps the call below the 5s interval.
jsonpath_value() {
  local kind="$1" name="$2" path="$3" out
  if out="$(timeout 4 kubectl get "$kind" "$name" -n "$NAMESPACE" -o "jsonpath=${path}" 2>/dev/null)"; then
    echo "${out:-0}"
  else
    echo ""
  fi
}

# Same failure/empty discipline as jsonpath_value: a failed kubectl call
# yields empty (NaN in the chart), not a fabricated 0 restart count.
restart_sum() {
  local label="$1" out
  if out="$(timeout 4 kubectl get pods -n "$NAMESPACE" -l "app=$label" \
    -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}' 2>/dev/null)"; then
    echo "$out" | awk '{sum += $1} END {print sum + 0}'
  else
    echo ""
  fi
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

  # jsonpath_value / restart_sum already return either a genuine value, "0"
  # for a legitimately-absent field, or "" for a failed kubectl call — so the
  # :-0 fallbacks that used to mask scrape failures are intentionally gone.
  echo "${timestamp},${web_ready},${web_available},${web_spec},${jobs_ready},${jobs_available},${jobs_spec},${web_hpa_current},${web_hpa_desired},${jobs_hpa_current},${jobs_hpa_desired},${web_restarts},${jobs_restarts}" >> "$OUTPUT_FILE"
  sleep "$INTERVAL_SECONDS"
done
