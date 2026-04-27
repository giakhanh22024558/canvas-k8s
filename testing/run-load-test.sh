#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

BASE_URL="${BASE_URL:-http://canvas.io.vn}"
PROM_URL="${PROM_URL:-http://127.0.0.1:30090/api/v1/write}"
TEST_TYPE="${TEST_TYPE:-load}"
TEST_ID="${TEST_ID:-canvas-$(date +%Y%m%d-%H%M%S)}"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
RUN_DIR="$RESULTS_DIR/$TEST_ID"
LOG_FILE="$RUN_DIR/k6-summary.txt"
SNAPSHOT_FILE="$RUN_DIR/k8s-snapshots.csv"
K8S_SNAPSHOT_PID=""

if ! command -v k6 >/dev/null 2>&1; then
  echo "k6 is required but not installed."
  exit 1
fi

if [[ -z "${API_TOKEN:-}" ]]; then
  echo "API_TOKEN is required. Run ./testing/setup-env.sh once or export API_TOKEN before running."
  exit 1
fi

case "$TEST_TYPE" in
  smoke)
    export VUS="${VUS:-1}"
    export DURATION="${DURATION:-30s}"
    ;;
  load)
    export VUS="${VUS:-10}"
    export DURATION="${DURATION:-5m}"
    ;;
  stress)
    export STAGES_JSON="${STAGES_JSON:-[{\"duration\":\"2m\",\"target\":10},{\"duration\":\"3m\",\"target\":30},{\"duration\":\"3m\",\"target\":60},{\"duration\":\"2m\",\"target\":0}]}"
    ;;
  soak)
    export VUS="${VUS:-15}"
    export DURATION="${DURATION:-30m}"
    ;;
  long-stress)
    # Extended stress test — holds each VU level long enough for HPA to scale and stabilize
    # Total ~23 minutes: ramp to 10 → hold 5m → ramp to 30 → hold 5m → ramp to 60 → hold 5m → ramp down
    export STAGES_JSON="${STAGES_JSON:-[{\"duration\":\"2m\",\"target\":10},{\"duration\":\"5m\",\"target\":10},{\"duration\":\"2m\",\"target\":30},{\"duration\":\"5m\",\"target\":30},{\"duration\":\"2m\",\"target\":60},{\"duration\":\"5m\",\"target\":60},{\"duration\":\"2m\",\"target\":0}]}"
    ;;
  breakpoint)
    # Slowly ramp VUs until the system breaks — finds the saturation point
    # Ramps from 1 to 100 VUs over 20 minutes, holding each level for 2 minutes
    export STAGES_JSON="${STAGES_JSON:-[{\"duration\":\"2m\",\"target\":10},{\"duration\":\"2m\",\"target\":20},{\"duration\":\"2m\",\"target\":30},{\"duration\":\"2m\",\"target\":40},{\"duration\":\"2m\",\"target\":50},{\"duration\":\"2m\",\"target\":60},{\"duration\":\"2m\",\"target\":80},{\"duration\":\"2m\",\"target\":100},{\"duration\":\"2m\",\"target\":0}]}"
    ;;
  *)
    echo "Unsupported TEST_TYPE: $TEST_TYPE"
    echo "Use one of: smoke, load, stress, soak, long-stress, breakpoint"
    exit 1
    ;;
esac

token_len="${#API_TOKEN}"
token_preview="${API_TOKEN:0:6}...${API_TOKEN: -4}"
login_enabled="no"
submission_enabled="no"

if [[ -n "${TEST_LOGIN_EMAIL:-}" ]]; then
  login_enabled="yes"
fi

if [[ -n "${SUBMISSION_API_TOKEN:-}" ]]; then
  submission_enabled="yes"
fi

mkdir -p "$RUN_DIR"

# Capture cluster state immediately — before any pod restarts, cooldowns, or
# test activity mutates the environment. This is the ground truth snapshot:
# resource limits, HPA config, replica counts, and git commit at test time.
# The plotting script reads environment.env to draw the memory limit line.
if command -v kubectl >/dev/null 2>&1; then
  ensure_kubeconfig
  echo "Capturing cluster snapshot to $RUN_DIR/environment.env ..."
  bash "$SCRIPT_DIR/capture-cluster-env.sh" "$RUN_DIR/environment.env" || true
  if [[ -f "$RUN_DIR/environment.env" ]]; then
    echo ""
    echo "============================================================"
    echo "  CLUSTER SNAPSHOT — pre-test state"
    echo "============================================================"
    while IFS='=' read -r key value; do
      [[ -z "$key" || "$key" == \#* ]] && continue
      printf "  %-35s %s\n" "$key" "$value"
    done < "$RUN_DIR/environment.env"
    echo "============================================================"
    echo ""
  fi
else
  echo "kubectl not available — skipping cluster snapshot."
fi

cleanup() {
  if [[ -n "$K8S_SNAPSHOT_PID" ]] && kill -0 "$K8S_SNAPSHOT_PID" >/dev/null 2>&1; then
    kill "$K8S_SNAPSHOT_PID" >/dev/null 2>&1 || true
    wait "$K8S_SNAPSHOT_PID" 2>/dev/null || true
  fi
  if [[ -f "$RUN_DIR/metadata.env" ]] && ! grep -q "^ended_at=" "$RUN_DIR/metadata.env"; then
    echo "ended_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_DIR/metadata.env"
  fi
}

trap cleanup EXIT

echo "Starting k6 load test"
echo "Base URL: $BASE_URL"
echo "Prometheus write URL: $PROM_URL"
echo "Test profile: $TEST_TYPE"
echo "Test ID: $TEST_ID"
echo "Using API token: $token_preview (length: $token_len)"
echo "Login flow enabled: $login_enabled"
echo "Submission flow enabled: $submission_enabled"
{
  echo "test_id=$TEST_ID"
  echo "base_url=$BASE_URL"
  echo "prom_url=$PROM_URL"
  echo "test_type=$TEST_TYPE"
  echo "login_flow_enabled=$login_enabled"
  echo "submission_flow_enabled=$submission_enabled"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$RUN_DIR/metadata.env"

hpa_clean_start() {
  # Detect if HPA is active in the canvas namespace
  local hpa_count
  hpa_count=$(kubectl get hpa -n canvas --no-headers 2>/dev/null | wc -l)
  if [[ "$hpa_count" -eq 0 ]]; then
    echo "HPA not detected — skipping clean-start reset."
    return 0
  fi

  echo "HPA detected — performing clean start (scale to 1 replica + pod restart)..."

  # Clamp HPA maxReplicas to 1 BEFORE scaling down.
  # Without this, HPA reads stale high-CPU metrics from the previous test
  # and immediately re-scales back to 5 pods, defeating the clean start.
  echo "Clamping HPA maxReplicas to 1 to prevent scale-up during warmup..."
  kubectl patch hpa canvas-web  -n canvas -p '{"spec":{"maxReplicas":1}}' 2>/dev/null || true
  kubectl patch hpa canvas-jobs -n canvas -p '{"spec":{"maxReplicas":1}}' 2>/dev/null || true

  # Force replicas back to 1 immediately (bypasses HPA scale-down cooldown)
  kubectl scale deployment canvas-web  -n canvas --replicas=1 2>/dev/null || true
  kubectl scale deployment canvas-jobs -n canvas --replicas=1 2>/dev/null || true

  # Restart pods so restart counters reset to 0 in snapshots
  kubectl rollout restart deployment/canvas-web  -n canvas
  kubectl rollout restart deployment/canvas-jobs -n canvas

  # Wait until pods are fully ready before starting the test
  echo "Waiting for canvas-web rollout..."
  kubectl rollout status deployment/canvas-web  -n canvas --timeout=120s
  echo "Waiting for canvas-jobs rollout..."
  kubectl rollout status deployment/canvas-jobs -n canvas --timeout=120s

  # Readiness probe passing ≠ Rails fully warmed up. A Canvas pod needs
  # time after becoming "Ready" to finish loading gems, open DB connection
  # pools, and compile routes. Without this sleep, k6 setup() hits a
  # half-warm pod and retries, inflating early error metrics.
  #
  # 4 minutes (was 2): the extra time lets HPA evaluate at least 4 scrape
  # cycles of near-zero CPU before load starts, so the baseline is clean.
  # It also prevents the pod from being overwhelmed at the very first VU
  # ramp before HPA has had any chance to collect metrics and scale out.
  echo "Waiting 4 minutes for Rails to warm up after restart..."
  sleep 240

  # Restore HPA maxReplicas so it can scale freely during the actual test
  echo "Restoring HPA maxReplicas (web=5, jobs=3)..."
  kubectl patch hpa canvas-web  -n canvas -p '{"spec":{"maxReplicas":5}}' 2>/dev/null || true
  kubectl patch hpa canvas-jobs -n canvas -p '{"spec":{"maxReplicas":3}}' 2>/dev/null || true

  echo "Clean start complete. Current pod state:"
  kubectl get pods -n canvas
  echo "Current HPA state:"
  kubectl get hpa  -n canvas
  echo "Warmup complete — starting test."
}

if command -v kubectl >/dev/null 2>&1; then
  ensure_kubeconfig
  if kubectl get namespace canvas >/dev/null 2>&1; then
    hpa_clean_start
    bash "$SCRIPT_DIR/collect-k8s-snapshots.sh" "$SNAPSHOT_FILE" &
    K8S_SNAPSHOT_PID="$!"
    echo "Collecting Kubernetes snapshots to $SNAPSHOT_FILE"
  else
    echo "Skipping Kubernetes snapshot collection because namespace canvas is unavailable."
  fi
else
  echo "Skipping Kubernetes snapshot collection because kubectl is unavailable."
fi

K6_PROMETHEUS_RW_SERVER_URL="$PROM_URL" \
K6_PROMETHEUS_RW_TREND_STATS="p(50),p(95),p(99),avg,min,max" \
k6 run -o experimental-prometheus-rw --tag testid="$TEST_ID" "$SCRIPT_DIR/load_test/canvas-load.js" 2>&1 | tee "$LOG_FILE"

echo "ended_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_DIR/metadata.env"

echo "Finished load test with testid=$TEST_ID"
echo "Saved run output to $RUN_DIR"

TEST_ID="$TEST_ID" RESULTS_DIR="$RESULTS_DIR" bash "$SCRIPT_DIR/publish-results.sh"
