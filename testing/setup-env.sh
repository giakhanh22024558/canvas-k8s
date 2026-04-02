#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${TESTING_ENV_FILE:-$SCRIPT_DIR/testing.env}"

default_base_url="${BASE_URL:-http://canvas.io.vn}"
default_prom_url="${PROM_URL:-http://127.0.0.1:30090/api/v1/write}"
default_prometheus_query_url="${PROMETHEUS_URL:-http://127.0.0.1:30090}"
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

read -r -p "Enter Prometheus query URL [$default_prometheus_query_url]: " PROMETHEUS_QUERY_URL_INPUT
PROMETHEUS_QUERY_URL_VALUE="${PROMETHEUS_QUERY_URL_INPUT:-$default_prometheus_query_url}"

read -r -p "Enter results Git repo URL [$default_results_repo_url]: " RESULTS_REPO_URL_INPUT
RESULTS_REPO_URL_VALUE="${RESULTS_REPO_URL_INPUT:-$default_results_repo_url}"

read -r -p "Enter local results repo directory [$default_results_repo_dir]: " RESULTS_REPO_DIR_INPUT
RESULTS_REPO_DIR_VALUE="${RESULTS_REPO_DIR_INPUT:-$default_results_repo_dir}"

default_test_type="${TEST_TYPE:-load}"
read -r -p "Enter default test profile [$default_test_type]: " TEST_TYPE_INPUT
TEST_TYPE_VALUE="${TEST_TYPE_INPUT:-$default_test_type}"

default_login_email="${TEST_LOGIN_EMAIL:-}"
read -r -p "Enter optional Canvas login email for session tests [$default_login_email]: " TEST_LOGIN_EMAIL_INPUT
TEST_LOGIN_EMAIL_VALUE="${TEST_LOGIN_EMAIL_INPUT:-$default_login_email}"

default_login_password="${TEST_LOGIN_PASSWORD:-}"
read -r -s -p "Enter optional Canvas login password [hidden]: " TEST_LOGIN_PASSWORD_INPUT
echo
TEST_LOGIN_PASSWORD_VALUE="${TEST_LOGIN_PASSWORD_INPUT:-$default_login_password}"

default_submission_token="${SUBMISSION_API_TOKEN:-}"
read -r -s -p "Enter optional student submission API token [hidden]: " SUBMISSION_API_TOKEN_INPUT
echo
SUBMISSION_API_TOKEN_VALUE="${SUBMISSION_API_TOKEN_INPUT:-$default_submission_token}"

default_runs_per_scenario="${RUNS_PER_SCENARIO:-9}"
read -r -p "Enter default repeated runs per scenario [$default_runs_per_scenario]: " RUNS_PER_SCENARIO_INPUT
RUNS_PER_SCENARIO_VALUE="${RUNS_PER_SCENARIO_INPUT:-$default_runs_per_scenario}"

default_cooldown_seconds="${COOLDOWN_SECONDS:-600}"
read -r -p "Enter cooldown seconds between runs [$default_cooldown_seconds]: " COOLDOWN_SECONDS_INPUT
COOLDOWN_SECONDS_VALUE="${COOLDOWN_SECONDS_INPUT:-$default_cooldown_seconds}"

cat > "$ENV_FILE" <<EOF
BASE_URL=$BASE_URL_VALUE
API_TOKEN=$API_TOKEN_INPUT
PROM_URL=$PROM_URL_VALUE
PROMETHEUS_URL=$PROMETHEUS_QUERY_URL_VALUE
RESULTS_REPO_URL=$RESULTS_REPO_URL_VALUE
RESULTS_REPO_DIR=$RESULTS_REPO_DIR_VALUE
TEST_TYPE=$TEST_TYPE_VALUE
TEST_LOGIN_EMAIL=$TEST_LOGIN_EMAIL_VALUE
TEST_LOGIN_PASSWORD=$TEST_LOGIN_PASSWORD_VALUE
SUBMISSION_API_TOKEN=$SUBMISSION_API_TOKEN_VALUE
RUNS_PER_SCENARIO=$RUNS_PER_SCENARIO_VALUE
COOLDOWN_SECONDS=$COOLDOWN_SECONDS_VALUE
EOF

chmod 600 "$ENV_FILE"

echo "Saved local testing config to $ENV_FILE"
echo "Your testing scripts will now pick up API_TOKEN, BASE_URL, PROM_URL, PROMETHEUS_URL, and results repo settings automatically."
