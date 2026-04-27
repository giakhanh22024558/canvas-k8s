#!/bin/bash
# aggregate-results.sh — Gather, calculate statistics, and plot charts for an
# experiment across all runs.
#
# Usage:
#   EXPERIMENT_NAME=stage1-baseline bash testing/aggregate-results.sh
#
# Options (environment variables):
#   EXPERIMENT_NAME   Experiment prefix, e.g. stage1-baseline (required)
#   RESULTS_DIR       Root results directory (default: testing/results)
#   OUTPUT_DIR        Where to write charts/CSV (default: RESULTS_DIR/analysis-EXPERIMENT_NAME)
#   NO_PLOTS          Set to "true" to skip chart generation (CSV + console only)
#   PUSH_GIT          Set to "true" to git-add and push the analysis output (default: false)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

EXPERIMENT_NAME="${EXPERIMENT_NAME:-}"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
OUTPUT_DIR="${OUTPUT_DIR:-$RESULTS_DIR/analysis-${EXPERIMENT_NAME}}"
NO_PLOTS="${NO_PLOTS:-false}"
PUSH_GIT="${PUSH_GIT:-false}"

if [[ -z "$EXPERIMENT_NAME" ]]; then
  echo "ERROR: EXPERIMENT_NAME is required."
  echo "  Usage: EXPERIMENT_NAME=stage1-baseline bash testing/aggregate-results.sh"
  exit 1
fi

# ── Find Python (venv first, then system) ────────────────────────────────────
PYTHON=""
for candidate in \
  "$ROOT_DIR/.venv/bin/python3" \
  "$ROOT_DIR/.venv/bin/python" \
  "$(command -v python3 2>/dev/null)" \
  "$(command -v python  2>/dev/null)"; do
  if [[ -x "$candidate" ]]; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "ERROR: Python 3 not found. Activate your venv: source .venv/bin/activate"
  exit 1
fi
echo "Using Python: $PYTHON"

# ── Pull latest code so charts use most recent plotting logic ─────────────────
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BRANCH="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ -n "$BRANCH" ]]; then
  echo "Pulling latest code on branch $BRANCH ..."
  git -C "$ROOT_DIR" pull origin "$BRANCH" --rebase || echo "WARNING: git pull failed — using local code."
  echo ""
fi

# ── Run aggregation ───────────────────────────────────────────────────────────
echo "============================================================"
echo "  Aggregating results for: $EXPERIMENT_NAME"
echo "  Results dir : $RESULTS_DIR"
echo "  Output dir  : $OUTPUT_DIR"
echo "============================================================"
echo ""

EXTRA_ARGS=""
if [[ "$NO_PLOTS" == "true" ]]; then
  EXTRA_ARGS="--no-plots"
fi

"$PYTHON" "$SCRIPT_DIR/charts/aggregate_analysis.py" \
  --experiment "$EXPERIMENT_NAME" \
  --results-dir "$RESULTS_DIR" \
  --output-dir "$OUTPUT_DIR" \
  $EXTRA_ARGS

# ── Optional git push ─────────────────────────────────────────────────────────
if [[ "$PUSH_GIT" == "true" ]]; then
  echo ""
  echo "Pushing analysis output to git..."
  cd "$ROOT_DIR"
  git pull origin "$BRANCH" --rebase || true
  git add "testing/results/analysis-${EXPERIMENT_NAME}/"
  if git diff --cached --quiet; then
    echo "Nothing new to commit."
  else
    git commit -m "Add aggregate analysis for experiment: $EXPERIMENT_NAME"
    git push origin "$BRANCH"
    echo "Pushed."
  fi
fi

echo ""
echo "Results:"
echo "  Charts + CSV : $OUTPUT_DIR"
echo ""
echo "To push to git manually:"
echo "  git add testing/results/analysis-${EXPERIMENT_NAME}/"
echo "  git commit -m 'Add aggregate analysis for $EXPERIMENT_NAME'"
echo "  git push"
