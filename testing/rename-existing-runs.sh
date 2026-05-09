#!/bin/bash
# rename-existing-runs.sh — One-shot rename of legacy canvas-<timestamp>
# result folders to <EXPERIMENT_NAME>-<timestamp> using the EXPERIMENT_NAME
# stored in metadata.env. Skips folders that don't follow the legacy pattern
# or that don't have a metadata.env. Idempotent — already-renamed folders
# are left alone.
#
# Usage:
#   bash testing/rename-existing-runs.sh           # rename in-place
#   DRY_RUN=true bash testing/rename-existing-runs.sh  # preview only
#
# After running, regenerate aggregate analysis with the new prefix names if
# the old names had been baked into experiment manifests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
DRY_RUN="${DRY_RUN:-false}"

renamed=0
skipped=0
for d in "$RESULTS_DIR"/canvas-*; do
  [[ -d "$d" ]] || continue
  base="$(basename "$d")"
  meta="$d/metadata.env"
  if [[ ! -f "$meta" ]]; then
    echo "  SKIP $base — no metadata.env"
    ((skipped++)) || true
    continue
  fi

  # Pull EXPERIMENT_NAME from metadata.env. The matrix runner writes it as
  # `experiment_name=...` and run-load-test.sh stamps it on the test_id, but
  # individual single-host runs may not set it. test_id stamped by k6 has
  # the form <prefix>-YYYYMMDD-HHMMSS — use that as the truth source.
  test_id_in_meta="$(grep -m1 '^test_id=' "$meta" | cut -d= -f2- || true)"
  exp_name="$(grep -m1 '^experiment_name=' "$meta" | cut -d= -f2- || true)"

  # Strip the YYYYMMDD-HHMMSS timestamp suffix from base to get the legacy
  # prefix (always "canvas" for old runs). Replace it with EXPERIMENT_NAME.
  ts_suffix="${base##*-}"
  date_suffix="${base%-*}"
  date_suffix="${date_suffix##*-}"
  if [[ ! "$ts_suffix" =~ ^[0-9]{6}$ ]] || [[ ! "$date_suffix" =~ ^[0-9]{8}$ ]]; then
    echo "  SKIP $base — non-standard timestamp"
    ((skipped++)) || true
    continue
  fi
  timestamp="${date_suffix}-${ts_suffix}"

  if [[ -n "$exp_name" ]]; then
    new_prefix="$exp_name"
  elif [[ "$test_id_in_meta" =~ ^([^[:space:]]+)-[0-9]{8}-[0-9]{6}$ ]]; then
    new_prefix="${BASH_REMATCH[1]}"
  else
    echo "  SKIP $base — no EXPERIMENT_NAME hint in metadata"
    ((skipped++)) || true
    continue
  fi

  # If already in the form <prefix>-<ts> and prefix == new_prefix, nothing to do.
  if [[ "$base" == "${new_prefix}-${timestamp}" ]]; then
    echo "  OK   $base — already named correctly"
    continue
  fi

  new_name="${new_prefix}-${timestamp}"
  new_path="$RESULTS_DIR/$new_name"
  if [[ -e "$new_path" ]]; then
    echo "  SKIP $base → $new_name (target exists)"
    ((skipped++)) || true
    continue
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "  DRY  $base → $new_name"
  else
    mv "$d" "$new_path"
    echo "  REN  $base → $new_name"
    # Update test_id inside metadata too so plot_prometheus.py and
    # aggregate_timeseries.py both keep matching the right Prometheus series.
    sed -i "s|^test_id=.*|test_id=$new_name|" "$new_path/metadata.env"
  fi
  ((renamed++)) || true
done

echo ""
echo "Renamed: $renamed   Skipped: $skipped"
[[ "$DRY_RUN" == "true" ]] && echo "(dry-run only — re-run without DRY_RUN=true to apply)"
