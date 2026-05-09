#!/bin/bash

TESTING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTING_ENV_FILE="${TESTING_ENV_FILE:-$TESTING_DIR/testing.env}"
K3S_KUBECONFIG="${K3S_KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

load_testing_env() {
  # Read KEY=VALUE pairs from testing.env and export them ONLY when the
  # variable is not already set in the caller's environment. This makes the
  # file behave as a defaults file: command-line overrides like
  #   TEST_TYPE=staircase bash testing/run-load-test.sh
  # win over the saved default in testing.env.
  #
  # The previous `set -a; source ...; set +a` pattern unconditionally
  # overwrote the caller's vars, which silently flipped TEST_TYPE back to
  # whatever was saved when setup-env.sh was last run.
  if [[ ! -f "$TESTING_ENV_FILE" ]]; then
    return 0
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip blank lines and comments
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    # Match KEY=VALUE — KEY is shell-identifier, VALUE keeps quotes/spaces as-is
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      local key="${BASH_REMATCH[1]}"
      local value="${BASH_REMATCH[2]}"
      # Only set when caller hasn't already exported it.
      if [[ -z "${!key+x}" ]]; then
        # Strip surrounding single or double quotes if present
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
