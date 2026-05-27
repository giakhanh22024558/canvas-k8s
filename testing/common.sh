#!/bin/bash

TESTING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTING_ENV_FILE="${TESTING_ENV_FILE:-$TESTING_DIR/testing.env}"
K3S_KUBECONFIG="${K3S_KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

load_testing_env() {
  if [[ ! -f "$TESTING_ENV_FILE" ]]; then
    return 0
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      local key="${BASH_REMATCH[1]}"
      local value="${BASH_REMATCH[2]}"
      if [[ -z "${!key+x}" ]]; then
        if [[ "$value" =~ ^\"(.*)\"$ ]] || [[ "$value" =~ ^\'(.*)\'$ ]]; then
          value="${BASH_REMATCH[1]}"
        fi
        export "$key=$value"
      fi
    fi
  done < "$TESTING_ENV_FILE"
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
