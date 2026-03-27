#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${TESTING_ENV_FILE:-$SCRIPT_DIR/testing.env}"

default_base_url="${BASE_URL:-http://canvas.io.vn}"
default_prom_url="${PROM_URL:-http://127.0.0.1:30090/api/v1/write}"
default_rclone_remote="${RCLONE_REMOTE:-}"
default_gdrive_folder_id="${GDRIVE_FOLDER_ID:-1xZTs4SQBAtJhCcMfRSto9neAn9eVFf1A}"

read -r -p "Enter Canvas base URL [$default_base_url]: " BASE_URL_INPUT
BASE_URL_VALUE="${BASE_URL_INPUT:-$default_base_url}"

read -r -s -p "Enter Canvas API token: " API_TOKEN_INPUT
echo

if [[ -z "$API_TOKEN_INPUT" ]]; then
  echo "API token is required"
  exit 1
fi

read -r -p "Enter Prometheus remote write URL [$default_prom_url]: " PROM_URL_INPUT
PROM_URL_VALUE="${PROM_URL_INPUT:-$default_prom_url}"

read -r -p "Enter rclone remote name for Google Drive [$default_rclone_remote]: " RCLONE_REMOTE_INPUT
RCLONE_REMOTE_VALUE="${RCLONE_REMOTE_INPUT:-$default_rclone_remote}"

read -r -p "Enter Google Drive folder ID [$default_gdrive_folder_id]: " GDRIVE_FOLDER_ID_INPUT
GDRIVE_FOLDER_ID_VALUE="${GDRIVE_FOLDER_ID_INPUT:-$default_gdrive_folder_id}"

cat > "$ENV_FILE" <<EOF
BASE_URL=$BASE_URL_VALUE
API_TOKEN=$API_TOKEN_INPUT
PROM_URL=$PROM_URL_VALUE
RCLONE_REMOTE=$RCLONE_REMOTE_VALUE
GDRIVE_FOLDER_ID=$GDRIVE_FOLDER_ID_VALUE
EOF

chmod 600 "$ENV_FILE"

echo "Saved local testing config to $ENV_FILE"
echo "Your testing scripts will now pick up API_TOKEN, BASE_URL, PROM_URL, and Drive upload settings automatically."
