#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${TESTING_ENV_FILE:-$SCRIPT_DIR/testing.env}"

default_base_url="${BASE_URL:-http://canvas.io.vn}"
default_prom_url="${PROM_URL:-http://127.0.0.1:30090/api/v1/write}"
default_results_repo_url="${RESULTS_REPO_URL:-https://github.com/giakhanh22024558/canvas-k8s-results.git}"
default_results_repo_dir="${RESULTS_REPO_DIR:-$SCRIPT_DIR/results-publish-repo}"

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

read -r -p "Enter results Git repo URL [$default_results_repo_url]: " RESULTS_REPO_URL_INPUT
RESULTS_REPO_URL_VALUE="${RESULTS_REPO_URL_INPUT:-$default_results_repo_url}"

read -r -p "Enter local results repo directory [$default_results_repo_dir]: " RESULTS_REPO_DIR_INPUT
RESULTS_REPO_DIR_VALUE="${RESULTS_REPO_DIR_INPUT:-$default_results_repo_dir}"

cat > "$ENV_FILE" <<EOF
BASE_URL=$BASE_URL_VALUE
API_TOKEN=$API_TOKEN_INPUT
PROM_URL=$PROM_URL_VALUE
RESULTS_REPO_URL=$RESULTS_REPO_URL_VALUE
RESULTS_REPO_DIR=$RESULTS_REPO_DIR_VALUE
EOF

chmod 600 "$ENV_FILE"

echo "Saved local testing config to $ENV_FILE"
echo "Your testing scripts will now pick up API_TOKEN, BASE_URL, PROM_URL, and results repo settings automatically."
