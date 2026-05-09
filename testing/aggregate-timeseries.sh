#!/bin/bash
# aggregate-timeseries.sh — Generate cross-run mean ± std time-series charts.
#
# Usage:
#   EXPERIMENT_NAME=stage5-hpa-tuned bash testing/aggregate-timeseries.sh
#
# Options:
#   EXPERIMENT_NAME   Experiment prefix (required)
#   RESULTS_DIR       Defaults to testing/results
#   OUTPUT_DIR        Defaults to RESULTS_DIR/analysis-<experiment>
#   STEP_SECONDS      Grid resolution (default 15)
#   PUSH_GIT          Set to "true" to commit + push the charts (default false)
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

if [[ -z "$EXPERIMENT_NAME" ]]; then
  echo "ERROR: EXPERIMENT_NAME is required."
  echo "  Usage: EXPERIMENT_NAME=stage5-hpa-tuned bash testing/aggregate-timeseries.sh"
  exit 1
fi

# ── Pull latest run folders from origin (load gen auto-pushed them) ─────────
# When k6 runs on a separate instance with AUTO_PUSH_RESULTS=true, the load gen
# commits canvas-* folders to origin. Aggregator pulls here so it sees them.
# Skip with FETCH_RESULTS=false (e.g. for offline reruns).
FETCH_RESULTS="${FETCH_RESULTS:-true}"

if [[ "$FETCH_RESULTS" == "true" ]]; then
  AGG_BRANCH="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
  if [[ -n "$AGG_BRANCH" ]]; then
    echo "Pulling latest run data from origin/$AGG_BRANCH (load gen auto-push) ..."
    git -C "$ROOT_DIR" pull origin "$AGG_BRANCH" --rebase --autostash 2>&1 | tail -5 \
      || echo "WARN: git pull failed. Continuing with local files."
    echo ""
  fi
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

# Note: git pull (FETCH_RESULTS step) above already brought in the latest code,
# so plotting fixes are applied. No extra pull here.
BRANCH="${AGG_BRANCH:-$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)}"

mkdir -p "$OUTPUT_DIR"

"$PYTHON" "$SCRIPT_DIR/charts/aggregate_timeseries.py" \
  --experiment "$EXPERIMENT_NAME" \
  --results-dir "$RESULTS_DIR" \
  --prometheus-url "$PROM_QUERY_URL" \
  --output-dir "$OUTPUT_DIR" \
  --step-seconds "$STEP_SECONDS"

# ── Optional push ─────────────────────────────────────────────────────────────
if [[ "$PUSH_GIT" == "true" ]]; then
  echo ""
  echo "Pushing charts to git..."
  cd "$ROOT_DIR"
  git pull origin "$BRANCH" --rebase || true
  git add "testing/results/analysis-${EXPERIMENT_NAME}/"
  if git diff --cached --quiet; then
    echo "Nothing new to commit."
  else
    git commit -m "Add cross-run time-series charts for ${EXPERIMENT_NAME}"
    git push origin "$BRANCH"
    echo "Pushed."
  fi
fi

echo ""
echo "Charts written to: $OUTPUT_DIR/timeseries_*.png"
