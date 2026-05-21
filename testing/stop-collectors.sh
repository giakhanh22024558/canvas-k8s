#!/bin/bash
# stop-collectors.sh — Kill all background collectors started by
# start-collectors.sh by reading the PID file it wrote.
#
# Usage:
#   bash testing/stop-collectors.sh
set -euo pipefail

PID_FILE="${COLLECTORS_PID_FILE:-/tmp/collectors.pids}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No PID file at $PID_FILE — nothing to stop."
  exit 0
fi

while read -r pid; do
  [[ -z "$pid" ]] && continue
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null && echo "  stopped pid=$pid"
  else
    echo "  pid=$pid not running"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
echo "Done."
