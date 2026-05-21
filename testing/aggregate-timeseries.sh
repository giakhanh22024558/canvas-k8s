#!/bin/bash
# aggregate-timeseries.sh — One-shot cross-run aggregation for an experiment.
#
# Produces, in testing/results/analysis-<experiment>/:
#   1. cross-run mean ± std TIME-SERIES charts (aggregate_timeseries.py)
#   2. an aggregate STATS TABLE — aggregate_stats_<experiment>.csv with
#      mean/std/min/max/median per metric — plus the bar-summary chart
#      (aggregate_analysis.py --no-boxplots)
#
# Per-metric box/strip plots are deliberately skipped: for the 3-run thesis
# design they add nothing over the stats table. Set NO_STATS=true to get the
# time-series charts only.
#
# Usage:
#   EXPERIMENT_NAME=stage3-hpa bash testing/aggregate-timeseries.sh
#
# Options:
#   EXPERIMENT_NAME   Experiment prefix (required)
#   RESULTS_DIR       Defaults to testing/results
#   OUTPUT_DIR        Defaults to RESULTS_DIR/analysis-<experiment>
#   STEP_SECONDS      Grid resolution for time-series (default 15)
#   NO_STATS          "true" → skip the stats table, time-series charts only
#   PUSH_GIT          "true" → commit + push the analysis output (default false)
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
# Saturation markers — defaults off (only meaningful for breakpoint).
# Auto-enable when EXPERIMENT_NAME contains "breakpoint" so the most
# common use case (Stage 2/breakpoint aggregate) just works.
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

# ── Optional: pull canvas-* run folders from remote load gen ─────────────────
# Set LOADGEN_SSH_HOST in testing.env (e.g. "ubuntu@172.31.6.227") when k6 runs
# on a separate instance. Skipped silently if unset.
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

# ── Find Python (venv first) ──────────────────────────────────────────────────
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

# ── Pull latest plotting code ─────────────────────────────────────────────────
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

# ── Statistical aggregate: mean ± std table ───────────────────────────────────
# Runs aggregate_analysis.py for the same experiment to emit
# aggregate_stats_<experiment>.csv (mean/std/min/max/median per metric) plus
# the bar-summary chart. Box/strip plots are skipped (--no-boxplots) — for a
# 3-run thesis they add no information over the table. Pass NO_STATS=true to
# skip this step and produce time-series charts only.
if [[ "${NO_STATS:-false}" != "true" ]]; then
  echo ""
  echo "Generating aggregate stats table (mean ± std)..."
  "$PYTHON" "$SCRIPT_DIR/charts/aggregate_analysis.py" \
    --experiment "$EXPERIMENT_NAME" \
    --results-dir "$RESULTS_DIR" \
    --output-dir "$OUTPUT_DIR" \
    --no-boxplots
fi

# ── Optional push ─────────────────────────────────────────────────────────────
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
