#!/bin/bash
# hpa-clean-start.sh — Reset the canvas namespace into a clean baseline state
# before a load test starts. Idempotent and safe to call when no HPA exists.
#
# Behaviour when HPA is present:
#   1. Capture the ORIGINAL maxReplicas of each HPA so we can restore them
#      exactly after warmup (previous versions hard-coded web=5, jobs=3
#      which was wrong for Stage 3's max=3,2 configuration).
#   2. Clamp HPA maxReplicas to 1 so stale CPU metrics from the previous run
#      cannot trigger a scale-up before we have time to scale down.
#   3. Scale the deployments to 1 replica.
#   4. `rollout restart` both deployments so restart counters reset to 0 in
#      post-run snapshots.
#   5. Wait for the rollouts to become Ready.
#   6. Sleep 4 minutes so Rails finishes warming up (probes can pass before
#      gems/routes/DB-pool are fully loaded) and HPA collects clean
#      near-zero baseline samples.
#   7. Restore the ORIGINAL maxReplicas captured in step 1.
#
# Invoked locally by run-load-test.sh on single-host setups, and via SSH from
# the load gen on split-host setups. Keeping it standalone gives one source of
# truth no matter where the test runner is.
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

# Capture original HPA maxReplicas so we restore exactly what the user
# deployed (whether that's Stage 3's 3/2 or Stage 4's 5/3 or anything
# else). Fall back to a safe default only if the HPA doesn't exist yet
# or the query fails.
ORIG_WEB_MAX=$(kubectl get hpa canvas-web  -n "$NAMESPACE" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || echo "")
ORIG_JOBS_MAX=$(kubectl get hpa canvas-jobs -n "$NAMESPACE" -o jsonpath='{.spec.maxReplicas}' 2>/dev/null || echo "")
ORIG_WEB_MAX=${ORIG_WEB_MAX:-3}
ORIG_JOBS_MAX=${ORIG_JOBS_MAX:-2}
echo "Original HPA maxReplicas: web=${ORIG_WEB_MAX}, jobs=${ORIG_JOBS_MAX} (will restore after warmup)"

# Clamp HPA maxReplicas to 1 BEFORE scaling down. Without this, HPA reads
# stale high-CPU metrics from the previous test and immediately re-scales
# back, defeating the clean start.
echo "Clamping HPA maxReplicas to 1 to prevent scale-up during warmup..."
kubectl patch hpa canvas-web  -n "$NAMESPACE" -p '{"spec":{"maxReplicas":1}}' 2>/dev/null || true
kubectl patch hpa canvas-jobs -n "$NAMESPACE" -p '{"spec":{"maxReplicas":1}}' 2>/dev/null || true

# Force replicas back to 1 immediately (bypasses HPA scale-down cooldown)
kubectl scale deployment canvas-web  -n "$NAMESPACE" --replicas=1 2>/dev/null || true
kubectl scale deployment canvas-jobs -n "$NAMESPACE" --replicas=1 2>/dev/null || true

# Restart pods so restart counters reset to 0 in snapshots
kubectl rollout restart deployment/canvas-web  -n "$NAMESPACE"
kubectl rollout restart deployment/canvas-jobs -n "$NAMESPACE"

echo "Waiting for canvas-web rollout..."
kubectl rollout status deployment/canvas-web  -n "$NAMESPACE" --timeout=120s
echo "Waiting for canvas-jobs rollout..."
kubectl rollout status deployment/canvas-jobs -n "$NAMESPACE" --timeout=120s

# Readiness probe passing ≠ Rails fully warmed up. A Canvas pod needs time
# after becoming "Ready" to finish loading gems, open DB connection pools,
# and compile routes. Without this sleep, k6 setup() hits a half-warm pod
# and retries, inflating early error metrics.
#
# 4 minutes: lets HPA evaluate at least 4 scrape cycles of near-zero CPU
# before load starts, so the baseline is clean. Also prevents the pod from
# being overwhelmed at the very first VU ramp before HPA has had any chance
# to collect metrics and scale out.
WARMUP_SECONDS="${WARMUP_SECONDS:-240}"
echo "Waiting ${WARMUP_SECONDS}s for Rails to warm up after restart..."
sleep "$WARMUP_SECONDS"

# Restore the ORIGINAL HPA maxReplicas captured at the top of the script.
# Preserving the user's configured maxReplicas means this script is correct
# for any HPA configuration without per-stage editing.
echo "Restoring HPA maxReplicas (web=${ORIG_WEB_MAX}, jobs=${ORIG_JOBS_MAX})..."
kubectl patch hpa canvas-web  -n "$NAMESPACE" -p "{\"spec\":{\"maxReplicas\":${ORIG_WEB_MAX}}}" 2>/dev/null || true
kubectl patch hpa canvas-jobs -n "$NAMESPACE" -p "{\"spec\":{\"maxReplicas\":${ORIG_JOBS_MAX}}}" 2>/dev/null || true

echo "Clean start complete. Current pod state:"
kubectl get pods -n "$NAMESPACE"
echo "Current HPA state:"
kubectl get hpa  -n "$NAMESPACE"
echo "Warmup complete — starting test."
