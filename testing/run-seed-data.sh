#!/bin/bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://canvas.io.vn:30080}"

if [[ -z "${API_TOKEN:-}" ]]; then
  echo "API_TOKEN is required"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3 or python is required"
  exit 1
fi

"$PYTHON_BIN" "$(dirname "$0")/seed_canvas_data.py"
