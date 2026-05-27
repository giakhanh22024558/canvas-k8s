#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

EXPERIMENT_NAME="${EXPERIMENT_NAME:-}"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
OUTPUT_DIR="${OUTPUT_DIR:-$RESULTS_DIR/analysis-${EXPERIMENT_NAME}}"
STEP_SECONDS="${STEP_SECONDS:-15}"
PUSH_GIT="${PUSH_GIT:-false}"
if [[ -z "${SATURATION_MARKERS:-}" ]]; then
  if [[ "$EXPERIMENT_NAME" == *breakpoint* ]]; then
    SATURATION_MARKERS="on"
  else
    SATURATION_MARKERS="off"
  fi
fi
export SATURATION_MARKERS

if [[ -z "$EXPERIMENT_NAME" ]]; then
  echo "ERROR: EXPERIMENT_NAME is required."
  echo "  Usage: EXPERIMENT_NAME=stage5-hpa-tuned bash testing/aggregate-timeseries.sh"
  exit 1
fi

LOADGEN_SSH_HOST="${LOADGEN_SSH_HOST:-}"
LOADGEN_RESULTS_DIR="${LOADGEN_RESULTS_DIR:-/home/ubuntu/canvas-k8s/testing/results}"
LOADGEN_SSH_KEY="${LOADGEN_SSH_KEY:-}"

if [[ -n "$LOADGEN_SSH_HOST" ]]; then
  SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
  if [[ -n "$LOADGEN_SSH_KEY" ]]; then
    SSH_OPTS="$SSH_OPTS -i $LOADGEN_SSH_KEY"
  fi
  echo "Syncing canvas-* run folders from load gen ($LOADGEN_SSH_HOST) ..."
  mkdir -p "$RESULTS_DIR"
  rsync -az --update --info=stats1 -e "ssh $SSH_OPTS" \
    --include='canvas-*/' --include='canvas-*/**' --exclude='*' \
    "$LOADGEN_SSH_HOST:$LOADGEN_RESULTS_DIR/" \
    "$RESULTS_DIR/" \
    || { echo "ERROR: rsync from load gen failed"; exit 1; }
  echo "Sync complete."
  echo ""
fi

PYTHON=""
for c in "$ROOT_DIR/.venv/bin/python3" "$ROOT_DIR/.venv/bin/python" \
         "$(command -v python3 2>/dev/null)" "$(command -v python 2>/dev/null)"; do
  if [[ -x "$c" ]]; then PYTHON="$c"; break; fi
done
if [[ -z "$PYTHON" ]]; then
  echo "ERROR: Python 3 not found. Activate venv: source .venv/bin/activate"
  exit 1
fi
echo "Using Python: $PYTHON"

PROM_QUERY_URL="$(prometheus_query_url)"
echo "Prometheus URL: $PROM_QUERY_URL"
echo ""

BRANCH="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ -n "$BRANCH" ]]; then
  echo "Pulling latest code on branch $BRANCH ..."
  git -C "$ROOT_DIR" pull origin "$BRANCH" --rebase || \
    echo "WARNING: git pull failed — using local code."
  echo ""
fi

mkdir -p "$OUTPUT_DIR"

PY_ARGS=(
  "$SCRIPT_DIR/charts/aggregate_timeseries.py"
  --experiment "$EXPERIMENT_NAME"
  --results-dir "$RESULTS_DIR"
  --prometheus-url "$PROM_QUERY_URL"
  --output-dir "$OUTPUT_DIR"
  --step-seconds "$STEP_SECONDS"
)
if [[ "${SATURATION_MARKERS,,}" == "on" ]]; then
  PY_ARGS+=(--saturation-markers)
  echo "Saturation markers: enabled (auto-detected breakpoint experiment)"
fi

"$PYTHON" "${PY_ARGS[@]}"

if [[ "${NO_STATS:-false}" != "true" ]]; then
  echo ""
  echo "Generating aggregate stats table (mean ± std)..."
  "$PYTHON" "$SCRIPT_DIR/charts/aggregate_analysis.py" \
    --experiment "$EXPERIMENT_NAME" \
    --results-dir "$RESULTS_DIR" \
    --output-dir "$OUTPUT_DIR" \
    --no-boxplots
fi

if [[ "$PUSH_GIT" == "true" ]]; then
  echo ""
  echo "Pushing charts to git..."
  cd "$ROOT_DIR"
  git pull origin "$BRANCH" --rebase || true
  git add -A "testing/results/analysis-${EXPERIMENT_NAME}/"
  if git diff --cached --quiet; then
    echo "Nothing new to commit."
  else
    git commit -m "Add cross-run time-series + aggregate stats for ${EXPERIMENT_NAME}"
    git push origin "$BRANCH"
    echo "Pushed."
  fi
fi

echo ""
echo "Charts written to: $OUTPUT_DIR/timeseries_*.png"
