#!/bin/bash
# capture-cluster-env.sh — Capture a comprehensive pre-test snapshot of the
# cluster state into both a machine-readable env file and a human-readable
# bundle of kubectl outputs. The snapshot is the ground-truth reference for
# verifying a run was valid after the fact.
#
# Captured into <output-dir>/environment.env (key=value):
#   - git commit, node count, captured_at timestamp
#   - Resource requests + limits (CPU/memory) for canvas-web and canvas-jobs
#     read both from the deployment SPEC and from the LIVE pod (so any
#     post-spec patch via `kubectl set resources` is visible)
#   - HPA min/max for web/jobs (or "—" if no HPA)
#   - VPA recommendation target (cpu, memory) for web/jobs if a VPA exists
#   - Replica counts: spec, ready, current pods
#   - Baseline restart counter for web/jobs (so post-run delta = in-test events)
#   - Cooldown / seed / base URL knobs from the calling environment
#
# Additionally writes <output-dir>/cluster-snapshot.txt with human-readable:
#   - `kubectl get pods -o wide` for the canvas namespace
#   - `kubectl describe deployment` summary
#   - `kubectl get hpa -o yaml` if HPA present
#   - `kubectl get vpa -o yaml` if VPA present
#   - Last 50 events on the canvas namespace
#
# Usage:
#   bash testing/capture-cluster-env.sh <output-dir-or-file>
#
# Backward-compat: if <output-dir-or-file> ends in .env, treat it as the
# environment.env path and place cluster-snapshot.txt in the same parent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

OUTPUT_ARG="${1:-}"
NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"

if [[ -z "$OUTPUT_ARG" ]]; then
  echo "Usage: $0 <output-dir-or-environment.env-path>"
  exit 1
fi

# Resolve env-file path + snapshot dir
if [[ "$OUTPUT_ARG" == *.env ]]; then
  ENV_FILE="$OUTPUT_ARG"
  SNAP_DIR="$(dirname "$OUTPUT_ARG")"
else
  ENV_FILE="$OUTPUT_ARG/environment.env"
  SNAP_DIR="$OUTPUT_ARG"
fi
mkdir -p "$SNAP_DIR"
SNAP_TXT="$SNAP_DIR/cluster-snapshot.txt"

# ── helpers ───────────────────────────────────────────────────────────────────

kget() {
  kubectl get "$@" -n "$NAMESPACE" 2>/dev/null || true
}

jp_spec() {
  # jsonpath from deployment spec
  local kind="$1" name="$2" path="$3"
  kubectl get "$kind" "$name" -n "$NAMESPACE" -o "jsonpath=$path" 2>/dev/null || true
}

jp_pod() {
  # Read the first live pod's container resource for the deployment selector.
  # Reflects post-spec patches (kubectl set resources, VPA Auto, etc.) that
  # the deployment.spec snapshot may not show.
  local label="$1" path="$2"
  local pod
  pod="$(kubectl get pods -n "$NAMESPACE" -l "app=$label" --field-selector=status.phase=Running --no-headers 2>/dev/null | head -1 | awk '{print $1}')"
  if [[ -z "$pod" ]]; then return; fi
  kubectl get pod "$pod" -n "$NAMESPACE" -o "jsonpath=$path" 2>/dev/null || true
}

vpa_target() {
  # Returns recommendation target for given container name; empty if no VPA.
  local container="$1" field="$2"
  kubectl get vpa -n "$NAMESPACE" -o json 2>/dev/null \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for item in data.get('items', []):
    recs = item.get('status', {}).get('recommendation', {}).get('containerRecommendations', [])
    for cr in recs:
        if cr.get('containerName') == '$container':
            print(cr.get('target', {}).get('$field', ''))
            sys.exit()
" 2>/dev/null || true
}

restart_baseline() {
  # Sum of restartCount across all running pods of a deployment.
  local label="$1"
  kubectl get pods -n "$NAMESPACE" -l "app=$label" \
    -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}' 2>/dev/null \
    | awk '{sum+=$1} END {print sum+0}'
}

# ── collect values ────────────────────────────────────────────────────────────

git_commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
node_count="$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"

# Deployment spec resources
web_cpu_req_spec="$(jp_spec deployment canvas-web '{.spec.template.spec.containers[0].resources.requests.cpu}')"
web_cpu_lim_spec="$(jp_spec deployment canvas-web '{.spec.template.spec.containers[0].resources.limits.cpu}')"
web_mem_req_spec="$(jp_spec deployment canvas-web '{.spec.template.spec.containers[0].resources.requests.memory}')"
web_mem_lim_spec="$(jp_spec deployment canvas-web '{.spec.template.spec.containers[0].resources.limits.memory}')"
jobs_cpu_req_spec="$(jp_spec deployment canvas-jobs '{.spec.template.spec.containers[0].resources.requests.cpu}')"
jobs_cpu_lim_spec="$(jp_spec deployment canvas-jobs '{.spec.template.spec.containers[0].resources.limits.cpu}')"
jobs_mem_req_spec="$(jp_spec deployment canvas-jobs '{.spec.template.spec.containers[0].resources.requests.memory}')"
jobs_mem_lim_spec="$(jp_spec deployment canvas-jobs '{.spec.template.spec.containers[0].resources.limits.memory}')"

# Live pod resources (reflects kubectl set resources / VPA Auto patches)
web_cpu_req_live="$(jp_pod canvas-web '{.spec.containers[0].resources.requests.cpu}')"
web_cpu_lim_live="$(jp_pod canvas-web '{.spec.containers[0].resources.limits.cpu}')"
web_mem_req_live="$(jp_pod canvas-web '{.spec.containers[0].resources.requests.memory}')"
web_mem_lim_live="$(jp_pod canvas-web '{.spec.containers[0].resources.limits.memory}')"
jobs_cpu_req_live="$(jp_pod canvas-jobs '{.spec.containers[0].resources.requests.cpu}')"
jobs_cpu_lim_live="$(jp_pod canvas-jobs '{.spec.containers[0].resources.limits.cpu}')"
jobs_mem_req_live="$(jp_pod canvas-jobs '{.spec.containers[0].resources.requests.memory}')"
jobs_mem_lim_live="$(jp_pod canvas-jobs '{.spec.containers[0].resources.limits.memory}')"

# Replica counts
web_replicas_spec="$(jp_spec deployment canvas-web '{.spec.replicas}')"
web_replicas_ready="$(jp_spec deployment canvas-web '{.status.readyReplicas}')"
jobs_replicas_spec="$(jp_spec deployment canvas-jobs '{.spec.replicas}')"
jobs_replicas_ready="$(jp_spec deployment canvas-jobs '{.status.readyReplicas}')"

# HPA settings (empty strings if no HPA)
web_hpa_min="$(jp_spec hpa canvas-web '{.spec.minReplicas}')"
web_hpa_max="$(jp_spec hpa canvas-web '{.spec.maxReplicas}')"
web_hpa_target_cpu="$(jp_spec hpa canvas-web '{.spec.metrics[0].resource.target.averageUtilization}')"
jobs_hpa_min="$(jp_spec hpa canvas-jobs '{.spec.minReplicas}')"
jobs_hpa_max="$(jp_spec hpa canvas-jobs '{.spec.maxReplicas}')"
jobs_hpa_target_cpu="$(jp_spec hpa canvas-jobs '{.spec.metrics[0].resource.target.averageUtilization}')"

# VPA targets (if VPA recommender is active)
web_vpa_target_cpu="$(vpa_target web cpu)"
web_vpa_target_mem="$(vpa_target web memory)"
jobs_vpa_target_cpu="$(vpa_target jobs cpu)"
jobs_vpa_target_mem="$(vpa_target jobs memory)"

# Restart counter baseline (so post-run delta = events that happened during the test)
web_restart_baseline="$(restart_baseline canvas-web)"
jobs_restart_baseline="$(restart_baseline canvas-jobs)"

# ── write environment.env ─────────────────────────────────────────────────────

cat > "$ENV_FILE" <<EOF
# Cluster snapshot captured immediately before load test starts.
# Used by post-run analysis to verify the run was performed under known state.
captured_at=$(date -Is)
git_commit=$git_commit
node_count=${node_count:-0}

# Resource requests/limits — DEPLOYMENT SPEC (what manifest declares)
web_cpu_request_spec=${web_cpu_req_spec:-}
web_cpu_limit_spec=${web_cpu_lim_spec:-}
web_memory_request_spec=${web_mem_req_spec:-}
web_memory_limit_spec=${web_mem_lim_spec:-}
jobs_cpu_request_spec=${jobs_cpu_req_spec:-}
jobs_cpu_limit_spec=${jobs_cpu_lim_spec:-}
jobs_memory_request_spec=${jobs_mem_req_spec:-}
jobs_memory_limit_spec=${jobs_mem_lim_spec:-}

# Resource requests/limits — LIVE POD (reflects kubectl set resources patches)
web_cpu_request=${web_cpu_req_live:-}
web_cpu_limit=${web_cpu_lim_live:-}
web_memory_request=${web_mem_req_live:-}
web_memory_limit=${web_mem_lim_live:-}
jobs_cpu_request=${jobs_cpu_req_live:-}
jobs_cpu_limit=${jobs_cpu_lim_live:-}
jobs_memory_request=${jobs_mem_req_live:-}
jobs_memory_limit=${jobs_mem_lim_live:-}

# Replica counts (spec vs running)
web_replicas_spec=${web_replicas_spec:-}
web_replicas_ready=${web_replicas_ready:-0}
jobs_replicas_spec=${jobs_replicas_spec:-}
jobs_replicas_ready=${jobs_replicas_ready:-0}

# HPA settings (empty if no HPA)
web_hpa_min=${web_hpa_min:-}
web_hpa_max=${web_hpa_max:-}
web_hpa_target_cpu_percent=${web_hpa_target_cpu:-}
jobs_hpa_min=${jobs_hpa_min:-}
jobs_hpa_max=${jobs_hpa_max:-}
jobs_hpa_target_cpu_percent=${jobs_hpa_target_cpu:-}

# VPA recommendation targets (empty if no VPA)
web_vpa_target_cpu=${web_vpa_target_cpu:-}
web_vpa_target_memory=${web_vpa_target_mem:-}
jobs_vpa_target_cpu=${jobs_vpa_target_cpu:-}
jobs_vpa_target_memory=${jobs_vpa_target_mem:-}

# Restart counter baseline (subtract from final to get in-test event count)
web_restart_baseline=${web_restart_baseline:-0}
jobs_restart_baseline=${jobs_restart_baseline:-0}

# Test-runner environment
cooldown_seconds=${COOLDOWN_SECONDS:-}
flush_redis_between_runs=${FLUSH_REDIS_BETWEEN_RUNS:-false}
seed_prefix=${SEED_PREFIX:-}
base_url=${BASE_URL:-}
EOF

# ── write human-readable cluster-snapshot.txt ─────────────────────────────────

{
  echo "============================================================"
  echo "  CLUSTER SNAPSHOT — captured at $(date -Is)"
  echo "  Namespace: $NAMESPACE   Git commit: $git_commit"
  echo "============================================================"
  echo
  echo "── Pods (canvas namespace) ──────────────────────────────────"
  kget pods -o wide
  echo
  echo "── Deployments ─────────────────────────────────────────────"
  kget deployment canvas-web canvas-jobs
  echo
  echo "── HPA ─────────────────────────────────────────────────────"
  kget hpa
  echo
  echo "── VPA recommendations ─────────────────────────────────────"
  if kubectl get vpa -n "$NAMESPACE" --no-headers 2>/dev/null | grep -q .; then
    kubectl get vpa -n "$NAMESPACE" -o yaml 2>/dev/null | \
      grep -E '^\s*(name|containerName|target|lowerBound|upperBound|cpu|memory|updateMode|recommendation):' | \
      head -80
  else
    echo "  (no VPA objects in namespace)"
  fi
  echo
  echo "── Recent events (last 50, oldest first) ───────────────────"
  kget events --sort-by='.lastTimestamp' | tail -50
} > "$SNAP_TXT" 2>&1

echo "Cluster snapshot written:"
echo "  $ENV_FILE"
echo "  $SNAP_TXT"
