#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results}"
CHARTS_DIR="${CHARTS_DIR:-$SCRIPT_DIR/charts/output}"
TEST_ID="${TEST_ID:-}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
GDRIVE_FOLDER_ID="${GDRIVE_FOLDER_ID:-}"
UPLOADS_DIR="${UPLOADS_DIR:-$SCRIPT_DIR/uploads}"

if ! command -v rclone >/dev/null 2>&1; then
  echo "rclone is required but not installed."
  exit 1
fi

if [[ -z "$RCLONE_REMOTE" ]]; then
  echo "RCLONE_REMOTE is required. Run ./testing/setup-env.sh or export RCLONE_REMOTE."
  exit 1
fi

if [[ -z "$GDRIVE_FOLDER_ID" ]]; then
  echo "GDRIVE_FOLDER_ID is required. Run ./testing/setup-env.sh or export GDRIVE_FOLDER_ID."
  exit 1
fi

if [[ -n "$TEST_ID" ]]; then
  RUN_DIR="$RESULTS_DIR/$TEST_ID"
else
  RUN_DIR="$(find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
  TEST_ID="$(basename "$RUN_DIR")"
fi

if [[ -z "${RUN_DIR:-}" || ! -d "$RUN_DIR" ]]; then
  echo "Could not find a load test run directory. Run ./testing/run-load-test.sh first or pass TEST_ID."
  exit 1
fi

if [[ ! -d "$CHARTS_DIR" ]]; then
  echo "Charts directory not found at $CHARTS_DIR. Generate charts first."
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
PACKAGE_DIR="$UPLOADS_DIR/${TEST_ID}-${STAMP}"
mkdir -p "$PACKAGE_DIR"

cp -R "$RUN_DIR" "$PACKAGE_DIR/run"
cp -R "$CHARTS_DIR" "$PACKAGE_DIR/charts"

cat > "$PACKAGE_DIR/upload-info.txt" <<EOF
test_id=$TEST_ID
packaged_at=$(date -Is)
base_url=${BASE_URL:-http://canvas.io.vn}
prom_url=${PROM_URL:-http://127.0.0.1:30090/api/v1/write}
source_run_dir=$RUN_DIR
source_charts_dir=$CHARTS_DIR
EOF

echo "Uploading $PACKAGE_DIR to Google Drive folder $GDRIVE_FOLDER_ID via remote $RCLONE_REMOTE"
rclone copy "$PACKAGE_DIR" "$RCLONE_REMOTE:" --drive-root-folder-id "$GDRIVE_FOLDER_ID" --create-empty-src-dirs --progress

echo "Upload complete."
echo "Uploaded package: $PACKAGE_DIR"
