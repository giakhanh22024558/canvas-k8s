#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

BASE_URL="${BASE_URL:-http://canvas.io.vn}"
PROM_URL="${PROM_URL:-http://127.0.0.1:30090/api/v1/write}"
TEST_TYPE="${TEST_TYPE:-load}"
if [[ -z "${TEST_ID:-}" ]]; then
  default_prefix="${EXPERIMENT_NAME:-canvas}"
  ts="$(date +%Y%m%d-%H%M%S)"
  if [[ -n "${RUN_LABEL:-}" ]]; then
    TEST_ID="${default_prefix}-${RUN_LABEL}-${ts}"
  else
    TEST_ID="${default_prefix}-${ts}"
  fi
fi
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
  staircase|long-stress)
    export STAGES_JSON="${STAGES_JSON:-[{\"duration\":\"2m\",\"target\":10},{\"duration\":\"5m\",\"target\":10},{\"duration\":\"2m\",\"target\":30},{\"duration\":\"5m\",\"target\":30},{\"duration\":\"2m\",\"target\":60},{\"duration\":\"5m\",\"target\":60},{\"duration\":\"2m\",\"target\":0}]}"
    ;;
  staircase-tuned)
    export STAGES_JSON="${STAGES_JSON:-[{\"duration\":\"2m\",\"target\":10},{\"duration\":\"5m\",\"target\":10},{\"duration\":\"2m\",\"target\":30},{\"duration\":\"5m\",\"target\":30},{\"duration\":\"2m\",\"target\":60},{\"duration\":\"5m\",\"target\":60},{\"duration\":\"2m\",\"target\":70},{\"duration\":\"5m\",\"target\":70},{\"duration\":\"5m\",\"target\":10},{\"duration\":\"5m\",\"target\":10}]}"
    ;;
  breakpoint)
    export STAGES_JSON="${STAGES_JSON:-[{\"duration\":\"2m\",\"target\":20},{\"duration\":\"2m\",\"target\":40},{\"duration\":\"2m\",\"target\":60},{\"duration\":\"2m\",\"target\":80},{\"duration\":\"2m\",\"target\":100},{\"duration\":\"2m\",\"target\":130},{\"duration\":\"2m\",\"target\":160},{\"duration\":\"2m\",\"target\":200},{\"duration\":\"2m\",\"target\":0}]}"
    ;;
  *)
    echo "Unsupported TEST_TYPE: $TEST_TYPE"
    echo "Use one of: smoke, load, stress, soak, staircase, staircase-tuned, breakpoint"
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

RUN_COMPLETED=false
KEEP_INCOMPLETE_RUNS="${KEEP_INCOMPLETE_RUNS:-false}"
K8S_SNAPSHOT_PID=""

trap cleanup EXIT

SUT_SSH_HOST="${SUT_SSH_HOST:-}"
SUT_REPO_DIR="${SUT_REPO_DIR:-/home/ubuntu/canvas-k8s}"
SUT_SSH_KEY="${SUT_SSH_KEY:-}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
[[ -n "$SUT_SSH_KEY" ]] && SSH_OPTS="$SSH_OPTS -i $SUT_SSH_KEY"

# Returns 0 (true) when cluster ops should run via SSH, 1 (false) when local.
remote_mode() { [[ -n "$SUT_SSH_HOST" ]]; }

snapshot_captured=false
if remote_mode; then
  echo "Capturing cluster snapshot via SSH ($SUT_SSH_HOST) ..."
  remote_snap_dir="/tmp/canvas-snap-$TEST_ID"
  if ssh $SSH_OPTS "$SUT_SSH_HOST" \
       "mkdir -p $remote_snap_dir && cd $SUT_REPO_DIR && bash testing/capture-cluster-env.sh $remote_snap_dir/environment.env"; then
    scp $SSH_OPTS "$SUT_SSH_HOST:$remote_snap_dir/environment.env"      "$RUN_DIR/environment.env"      2>/dev/null || true
    scp $SSH_OPTS "$SUT_SSH_HOST:$remote_snap_dir/cluster-snapshot.txt" "$RUN_DIR/cluster-snapshot.txt" 2>/dev/null || true
    ssh $SSH_OPTS "$SUT_SSH_HOST" "rm -rf $remote_snap_dir" || true
    snapshot_captured=true
  else
    echo "WARN: remote capture-cluster-env.sh failed — proceeding without snapshot."
  fi
elif command -v kubectl >/dev/null 2>&1; then
  ensure_kubeconfig
  echo "Capturing cluster snapshot to $RUN_DIR/ ..."
  bash "$SCRIPT_DIR/capture-cluster-env.sh" "$RUN_DIR/environment.env" || true
  snapshot_captured=true
else
  echo "kubectl not available and SUT_SSH_HOST unset — skipping cluster snapshot."
fi

if [[ "$snapshot_captured" == "true" ]]; then

  if [[ -f "$RUN_DIR/environment.env" ]]; then
    echo ""
    echo "============================================================"
    echo "  CLUSTER SNAPSHOT — pre-test state"
    echo "  (environment.env)"
    echo "============================================================"
    while IFS='=' read -r key value; do
      [[ -z "$key" || "$key" == \#* ]] && continue
      printf "  %-35s %s\n" "$key" "$value"
    done < "$RUN_DIR/environment.env"
    echo "============================================================"
  fi
  if [[ -f "$RUN_DIR/cluster-snapshot.txt" ]]; then
    echo ""
    echo "============================================================"
    echo "  CLUSTER SNAPSHOT — kubectl bundle"
    echo "  (cluster-snapshot.txt)"
    echo "============================================================"
    cat "$RUN_DIR/cluster-snapshot.txt"
    echo "============================================================"
    echo ""
  fi
fi

SKIP_CONFIRM="${SKIP_CONFIRM:-false}"
if [[ "$SKIP_CONFIRM" == "true" ]]; then
  echo "SKIP_CONFIRM=true — proceeding without operator confirmation."
else
  echo ""
  echo "Test configuration:"
  echo "  EXPERIMENT_NAME = ${EXPERIMENT_NAME:-<unset, defaulting to canvas>}"
  echo "  TEST_TYPE       = $TEST_TYPE"
  echo "  TEST_ID         = $TEST_ID"
  echo "  RUN_DIR         = $RUN_DIR"
  echo ""
  read -rp "Proceed with load test against the cluster shown above? [y/N]: " _confirm
  if [[ "${_confirm,,}" != "y" ]]; then
    echo "Aborted by operator. Removing empty run folder: $RUN_DIR"
    rm -rf "$RUN_DIR"
    exit 0
  fi
fi

cleanup() {
  if [[ -n "$K8S_SNAPSHOT_PID" ]] && kill -0 "$K8S_SNAPSHOT_PID" >/dev/null 2>&1; then
    kill "$K8S_SNAPSHOT_PID" >/dev/null 2>&1 || true
    wait "$K8S_SNAPSHOT_PID" 2>/dev/null || true
  fi

  if [[ -n "$SUT_SSH_HOST" && "$RUN_COMPLETED" != "true" ]]; then
    echo ""
    echo "Stopping collectors on SUT ($SUT_SSH_HOST) after incomplete run ..."
    ssh $SSH_OPTS "$SUT_SSH_HOST" \
      "cd $SUT_REPO_DIR && bash testing/stop-collectors.sh" \
      || echo "WARN: remote stop-collectors.sh failed — may need manual cleanup."
  fi

  if [[ -f "$RUN_DIR/metadata.env" ]] && ! grep -q "^ended_at=" "$RUN_DIR/metadata.env"; then
    echo "ended_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_DIR/metadata.env"
  fi

  # Garbage-collect the run folder if the test never produced a usable summary
  # AND the operator has not opted in to keeping incomplete runs for debugging.
  if [[ "$RUN_COMPLETED" != "true" && "$KEEP_INCOMPLETE_RUNS" != "true" && -d "$RUN_DIR" ]]; then
    echo ""
    echo "============================================================"
    echo "  Run did not complete successfully — removing folder:"
    echo "    $RUN_DIR"
    echo "  Override with KEEP_INCOMPLETE_RUNS=true to retain partial"
    echo "  output for debugging."
    echo "============================================================"
    rm -rf "$RUN_DIR"
  elif [[ "$RUN_COMPLETED" != "true" && "$KEEP_INCOMPLETE_RUNS" == "true" && -d "$RUN_DIR" ]]; then
    echo ""
    echo "WARN: run did not complete but KEEP_INCOMPLETE_RUNS=true — folder kept:"
    echo "  $RUN_DIR"
    # Mark the folder so post-run analysis tools can ignore it.
    echo "completed=false" >> "$RUN_DIR/metadata.env"
  fi
}

echo "Starting k6 load test"
echo "Base URL: $BASE_URL"
echo "Prometheus write URL: $PROM_URL"
echo "Test profile: $TEST_TYPE"
echo "Test ID: $TEST_ID"
echo "Using API token: $token_preview (length: $token_len)"
echo "Login flow enabled: $login_enabled"
echo "Submission flow enabled: $submission_enabled"

# NOTE: metadata.env (with started_at) is intentionally written LATER — after
# hpa-clean-start's warmup and the collector launch, immediately before k6.
# See the block just above the `k6 run` invocation for the rationale.

# HPA clean-start is implemented in testing/hpa-clean-start.sh — that script is
# the single source of truth. Local runs invoke it directly; remote runs SSH
# into the SUT and execute the same script from the SUT's checkout.
if remote_mode; then
  echo "Running HPA clean-start on SUT ($SUT_SSH_HOST) ..."
  ssh $SSH_OPTS "$SUT_SSH_HOST" \
    "cd $SUT_REPO_DIR && WARMUP_SECONDS=${WARMUP_SECONDS:-240} bash testing/hpa-clean-start.sh" \
    || echo "WARN: remote hpa-clean-start.sh failed — continuing anyway."
  # Cluster snapshot collection runs on the SUT via start-collectors.sh (next
  # block), so we deliberately skip the local collect-k8s-snapshots.sh here.
elif command -v kubectl >/dev/null 2>&1; then
  ensure_kubeconfig
  if kubectl get namespace canvas >/dev/null 2>&1; then
    bash "$SCRIPT_DIR/hpa-clean-start.sh"
    bash "$SCRIPT_DIR/collect-k8s-snapshots.sh" "$SNAPSHOT_FILE" &
    K8S_SNAPSHOT_PID="$!"
    echo "Collecting Kubernetes snapshots to $SNAPSHOT_FILE"
  else
    echo "Skipping Kubernetes snapshot collection because namespace canvas is unavailable."
  fi
else
  echo "Skipping Kubernetes snapshot collection because kubectl is unavailable."
fi

if remote_mode; then
  echo ""
  echo "Starting collectors on SUT ($SUT_SSH_HOST) ..."
  ssh $SSH_OPTS "$SUT_SSH_HOST" \
    "cd $SUT_REPO_DIR && bash testing/start-collectors.sh" \
    || echo "WARN: remote start-collectors.sh failed — continuing without on-SUT collectors."
fi

{
  echo "test_id=$TEST_ID"
  echo "experiment_name=${EXPERIMENT_NAME:-}"
  echo "base_url=$BASE_URL"
  echo "prom_url=$PROM_URL"
  echo "test_type=$TEST_TYPE"
  echo "login_flow_enabled=$login_enabled"
  echo "submission_flow_enabled=$submission_enabled"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$RUN_DIR/metadata.env"

# k6 exits non-zero when thresholds fail (exit code 108) — expected under stress
# conditions where error rate is high. Use || true so the matrix continues to
# the next run instead of stopping after the first threshold breach.
K6_PROMETHEUS_RW_SERVER_URL="$PROM_URL" \
K6_PROMETHEUS_RW_TREND_STATS="p(50),p(95),p(99),avg,min,max" \
k6 run -o experimental-prometheus-rw --tag testid="$TEST_ID" "$SCRIPT_DIR/load_test/canvas-load.js" 2>&1 | tee "$LOG_FILE" || true

if [[ -f "$LOG_FILE" ]] && grep -q "^[[:space:]]*http_reqs" "$LOG_FILE"; then
  RUN_COMPLETED=true
  echo "ended_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_DIR/metadata.env"
  echo "completed=true" >> "$RUN_DIR/metadata.env"
  echo "Finished load test with testid=$TEST_ID"
  echo "Saved run output to $RUN_DIR"
else
  echo "WARN: k6 did not produce a usable summary in $LOG_FILE"
  echo "      The run folder will be removed unless KEEP_INCOMPLETE_RUNS=true is set."
fi

echo "Raw data saved to $RUN_DIR"
echo "  k6-summary.txt, k8s-snapshots.csv, metadata.env, environment.env"

# Skip downstream publish if the run didn't complete — there's no useful
# data to upload, and cleanup() will remove the folder shortly anyway.
if [[ "$RUN_COMPLETED" != "true" ]]; then
  echo "Skipping finalize-run.sh: run did not complete successfully."
  exit 0
fi

if remote_mode; then
  echo ""
  echo "Triggering finalize-run.sh on SUT ($SUT_SSH_HOST) for $TEST_ID ..."
  if ssh $SSH_OPTS "$SUT_SSH_HOST" \
       "cd $SUT_REPO_DIR && TEST_ID=$TEST_ID bash testing/finalize-run.sh"; then
    if [[ -n "$RUN_DIR" && -d "$RUN_DIR" && "$RUN_DIR" == *"/testing/results/"* ]]; then
      echo "Finalize succeeded — removing redundant local run folder: $RUN_DIR"
      rm -rf "$RUN_DIR"
    fi
  else
    echo "WARN: remote finalize-run.sh failed. Run manually: ssh $SUT_SSH_HOST 'cd $SUT_REPO_DIR && TEST_ID=$TEST_ID bash testing/finalize-run.sh'"
    echo "      Local run folder kept for recovery: $RUN_DIR"
  fi
else
  echo "Run 'bash testing/finalize-run.sh' on the SUT to stop collectors and publish."
  echo "Tip: set SUT_SSH_HOST in testing.env to auto-orchestrate the full pipeline from this host."
fi
