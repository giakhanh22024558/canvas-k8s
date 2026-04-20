#!/bin/bash

TESTING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTING_ENV_FILE="${TESTING_ENV_FILE:-$TESTING_DIR/testing.env}"
K3S_KUBECONFIG="${K3S_KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

load_testing_env() {
  if [[ -f "$TESTING_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$TESTING_ENV_FILE"
    set +a
  fi
}

ensure_kubeconfig() {
  if [[ -z "${KUBECONFIG:-}" && -f "$K3S_KUBECONFIG" ]]; then
    export KUBECONFIG="$K3S_KUBECONFIG"
  fi
}

prometheus_query_url() {
  if [[ -n "${PROMETHEUS_URL:-}" ]]; then
    echo "$PROMETHEUS_URL"
  elif [[ -n "${PROM_URL:-}" ]]; then
    echo "${PROM_URL%/api/v1/write}"
  else
    echo "http://127.0.0.1:30090"
  fi
}
