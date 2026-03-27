#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
load_testing_env

if [[ -z "${API_TOKEN:-}" ]]; then
  echo "API_TOKEN is required"
  exit 1
fi

if [[ -z "${SEED_PREFIX:-}" ]]; then
  echo "SEED_PREFIX is required"
  exit 1
fi

if [[ -z "${BASE_URL:-}" ]]; then
  BASE_URL="http://canvas.io.vn"
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3 or python is required"
  exit 1
fi

"$PYTHON_BIN" "$SCRIPT_DIR/unseed_canvas_data.py"
