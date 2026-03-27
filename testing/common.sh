#!/bin/bash

TESTING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTING_ENV_FILE="${TESTING_ENV_FILE:-$TESTING_DIR/testing.env}"

load_testing_env() {
  if [[ -f "$TESTING_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$TESTING_ENV_FILE"
    set +a
  fi
}
