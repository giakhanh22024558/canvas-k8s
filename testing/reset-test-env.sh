#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-600}"
FLUSH_REDIS_BETWEEN_RUNS="${FLUSH_REDIS_BETWEEN_RUNS:-false}"
RESTART_DEPLOYMENTS="${RESTART_DEPLOYMENTS:-true}"

if [[ "$RESTART_DEPLOYMENTS" == "true" ]]; then
  echo "Restarting canvas-web and canvas-jobs deployments..."
  kubectl rollout restart deployment/canvas-web -n "$NAMESPACE"
  kubectl rollout restart deployment/canvas-jobs -n "$NAMESPACE"
  kubectl rollout status deployment/canvas-web -n "$NAMESPACE" --timeout=900s
  kubectl rollout status deployment/canvas-jobs -n "$NAMESPACE" --timeout=900s
fi

if [[ "$FLUSH_REDIS_BETWEEN_RUNS" == "true" ]]; then
  echo "Flushing Redis cache before the next run..."
  kubectl exec -n "$NAMESPACE" deployment/redis -- redis-cli FLUSHALL
fi

if [[ "$COOLDOWN_SECONDS" -gt 0 ]]; then
  echo ""
  echo "============================================================"
  echo "  CLUSTER STATE — before cooldown"
  echo "============================================================"
  kubectl get pods -n "$NAMESPACE" --no-headers \
    | awk '{printf "  %-45s %-12s %s\n", $1, $3, $4}' 2>/dev/null || true
  echo "------------------------------------------------------------"
  kubectl top pods -n "$NAMESPACE" --no-headers 2>/dev/null \
    | awk '{printf "  %-45s %-10s %s\n", $1, $2, $3}' || true
  echo "============================================================"
  echo ""
  echo "Cooling down for ${COOLDOWN_SECONDS}s..."
  sleep "$COOLDOWN_SECONDS"
fi

echo "Verifying pod readiness..."
kubectl wait --for=condition=Ready pod -l app=canvas-web -n "$NAMESPACE" --timeout=300s
kubectl wait --for=condition=Ready pod -l app=canvas-jobs -n "$NAMESPACE" --timeout=300s

echo "Test environment reset completed."
