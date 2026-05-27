#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env
ensure_kubeconfig

NAMESPACE="${SNAPSHOT_NAMESPACE:-canvas}"

hpa_count=$(kubectl get hpa -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
if [[ "$hpa_count" -eq 0 ]]; then
  echo "HPA not detected — skipping clean-start reset."
  exit 0
fi

echo "HPA detected — performing clean start (scale to 1 replica + pod restart)..."

ORIG_WEB_MAX=$(kubectl get hpa canvas-web  -n "$NAMESPACE" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || echo "")
ORIG_JOBS_MAX=$(kubectl get hpa canvas-jobs -n "$NAMESPACE" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || echo "")
ORIG_WEB_MAX=${ORIG_WEB_MAX:-3}
ORIG_JOBS_MAX=${ORIG_JOBS_MAX:-2}
echo "Original HPA maxReplicas: web=${ORIG_WEB_MAX}, jobs=${ORIG_JOBS_MAX} (will restore after warmup)"

echo "Clamping HPA maxReplicas to 1 to prevent scale-up during warmup..."
kubectl patch hpa canvas-web  -n "$NAMESPACE" -p '{"spec":{"maxReplicas":1}}' 2>/dev/null || true
kubectl patch hpa canvas-jobs -n "$NAMESPACE" -p '{"spec":{"maxReplicas":1}}' 2>/dev/null || true

kubectl scale deployment canvas-web  -n "$NAMESPACE" --replicas=1 2>/dev/null || true
kubectl scale deployment canvas-jobs -n "$NAMESPACE" --replicas=1 2>/dev/null || true

kubectl rollout restart deployment/canvas-web  -n "$NAMESPACE"
kubectl rollout restart deployment/canvas-jobs -n "$NAMESPACE"

echo "Waiting for canvas-web rollout..."
kubectl rollout status deployment/canvas-web  -n "$NAMESPACE" --timeout=120s
echo "Waiting for canvas-jobs rollout..."
kubectl rollout status deployment/canvas-jobs -n "$NAMESPACE" --timeout=120s

WARMUP_SECONDS="${WARMUP_SECONDS:-240}"
echo "Waiting ${WARMUP_SECONDS}s for Rails to warm up after restart..."
sleep "$WARMUP_SECONDS"

echo "Restoring HPA maxReplicas (web=${ORIG_WEB_MAX}, jobs=${ORIG_JOBS_MAX})..."
kubectl patch hpa canvas-web  -n "$NAMESPACE" -p "{\"spec\":{\"maxReplicas\":${ORIG_WEB_MAX}}}" 2>/dev/null || true
kubectl patch hpa canvas-jobs -n "$NAMESPACE" -p "{\"spec\":{\"maxReplicas\":${ORIG_JOBS_MAX}}}" 2>/dev/null || true

echo "Clean start complete. Current pod state:"
kubectl get pods -n "$NAMESPACE"
echo "Current HPA state:"
kubectl get hpa  -n "$NAMESPACE"
echo "Warmup complete — starting test."
