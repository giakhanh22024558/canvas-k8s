#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

OUTPUT_FILE="${1:-}"
NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Usage: ./testing/capture-cluster-env.sh <output-file>"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"

git_commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
node_count="$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')"
web_memory_limit="$(kubectl get deployment canvas-web -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}' 2>/dev/null || true)"
jobs_memory_limit="$(kubectl get deployment canvas-jobs -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}' 2>/dev/null || true)"
web_cpu_limit="$(kubectl get deployment canvas-web -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}' 2>/dev/null || true)"
jobs_cpu_limit="$(kubectl get deployment canvas-jobs -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}' 2>/dev/null || true)"
web_hpa_min="$(kubectl get hpa canvas-web -n "$NAMESPACE" -o jsonpath='{.spec.minReplicas}' 2>/dev/null || true)"
web_hpa_max="$(kubectl get hpa canvas-web -n "$NAMESPACE" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || true)"
jobs_hpa_min="$(kubectl get hpa canvas-jobs -n "$NAMESPACE" -o jsonpath='{.spec.minReplicas}' 2>/dev/null || true)"
jobs_hpa_max="$(kubectl get hpa canvas-jobs -n "$NAMESPACE" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || true)"

cat > "$OUTPUT_FILE" <<EOF
captured_at=$(date -Is)
git_commit=$git_commit
node_count=${node_count:-0}
web_memory_limit=${web_memory_limit:-}
jobs_memory_limit=${jobs_memory_limit:-}
web_cpu_limit=${web_cpu_limit:-}
jobs_cpu_limit=${jobs_cpu_limit:-}
web_hpa_min=${web_hpa_min:-}
web_hpa_max=${web_hpa_max:-}
jobs_hpa_min=${jobs_hpa_min:-}
jobs_hpa_max=${jobs_hpa_max:-}
cooldown_seconds=${COOLDOWN_SECONDS:-}
flush_redis_between_runs=${FLUSH_REDIS_BETWEEN_RUNS:-false}
seed_prefix=${SEED_PREFIX:-}
base_url=${BASE_URL:-}
EOF
