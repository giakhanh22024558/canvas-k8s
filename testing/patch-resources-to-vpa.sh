#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB="$REPO_ROOT/deployment/deployment-web.yaml"
JOBS="$REPO_ROOT/deployment/deployment-jobs.yaml"

# Default values — VPA target for requests, VPA upper bound for limits.
# Override per-run if VPA recommends different numbers.
WEB_CPU_REQ="${WEB_CPU_REQ:-813m}"
WEB_MEM_REQ="${WEB_MEM_REQ:-4400Mi}"   # 4.2Gi rounded up
WEB_CPU_LIM="${WEB_CPU_LIM:-4}"
WEB_MEM_LIM="${WEB_MEM_LIM:-8Gi}"

JOBS_CPU_REQ="${JOBS_CPU_REQ:-300m}"   # ~VPA target 296m
JOBS_MEM_REQ="${JOBS_MEM_REQ:-4Gi}"
JOBS_CPU_LIM="${JOBS_CPU_LIM:-2}"
JOBS_MEM_LIM="${JOBS_MEM_LIM:-4Gi}"

ts="$(date +%Y%m%d-%H%M%S)"
cp "$WEB"  "/tmp/deployment-web.bak.${ts}.yaml"
cp "$JOBS" "/tmp/deployment-jobs.bak.${ts}.yaml"
echo "Backups → /tmp/deployment-{web,jobs}.bak.${ts}.yaml"

python3 <<PY
import re, pathlib, sys

VALUES = {
    "web": {
        "cpu_req": "$WEB_CPU_REQ",
        "mem_req": "$WEB_MEM_REQ",
        "cpu_lim": '"$WEB_CPU_LIM"',
        "mem_lim": "$WEB_MEM_LIM",
    },
    "jobs": {
        "cpu_req": "$JOBS_CPU_REQ",
        "mem_req": "$JOBS_MEM_REQ",
        "cpu_lim": '"$JOBS_CPU_LIM"',
        "mem_lim": "$JOBS_MEM_LIM",
    },
}

def patch(path, kind):
    v = VALUES[kind]
    text = pathlib.Path(path).read_text()
    new_block = f"""          resources:
            requests:
              cpu: {v['cpu_req']}    # VPA target — Stage 2 onwards
              memory: {v['mem_req']}
            limits:
              cpu: {v['cpu_lim']}        # VPA upper bound
              memory: {v['mem_lim']}
"""
    pattern = re.compile(
        r"^( {10}resources:.*?)(?=^ {10}\w)",
        re.MULTILINE | re.DOTALL,
    )
    new_text, n = pattern.subn(new_block, text, count=1)
    if n != 1:
        print(f"ERROR: failed to find resources block in {path}", file=sys.stderr)
        sys.exit(2)
    pathlib.Path(path).write_text(new_text)
    print(f"  ✓ patched {path}")

patch("deployment/deployment-web.yaml",  "web")
patch("deployment/deployment-jobs.yaml", "jobs")
PY

echo ""
echo "=== canvas-web resources after patch ==="
grep -A6 '^ \{10\}resources:' "$WEB" | head -8
echo ""
echo "=== canvas-jobs resources after patch ==="
grep -A6 '^ \{10\}resources:' "$JOBS" | head -8
echo ""
echo "Done. Apply with:"
echo "  ./deploy.sh hpa         # for Stage 3"
echo "  PRESCALED_WEB_REPLICAS=2 PRESCALED_JOBS_REPLICAS=3 ./deploy.sh prescaled  # for Stage 2 max-pack"
