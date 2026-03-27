#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

BASE_URL="${BASE_URL:-http://canvas.io.vn}"
PROM_URL="${PROM_URL:-http://127.0.0.1:30090/api/v1/write}"
TEST_ID="${TEST_ID:-canvas-$(date +%Y%m%d-%H%M%S)}"

if ! command -v k6 >/dev/null 2>&1; then
  echo "k6 is required but not installed."
  exit 1
fi

if [[ -z "${API_TOKEN:-}" ]]; then
  echo "API_TOKEN is required. Run ./testing/setup-env.sh once or export API_TOKEN before running."
  exit 1
fi

token_len="${#API_TOKEN}"
token_preview="${API_TOKEN:0:6}...${API_TOKEN: -4}"

echo "Starting k6 load test"
echo "Base URL: $BASE_URL"
echo "Prometheus write URL: $PROM_URL"
echo "Test ID: $TEST_ID"
echo "Using API token: $token_preview (length: $token_len)"

K6_PROMETHEUS_RW_SERVER_URL="$PROM_URL" \
K6_PROMETHEUS_RW_TREND_STATS="p(95),p(99),avg,min,max" \
k6 run -o experimental-prometheus-rw --tag testid="$TEST_ID" "$SCRIPT_DIR/load_test/canvas-load.js"

echo "Finished load test with testid=$TEST_ID"
