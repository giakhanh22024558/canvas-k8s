#!/bin/bash
# patch-resources-to-naive.sh — Reset canvas-web and canvas-jobs deployment
# YAML resource blocks to the pre-VPA "naive engineer default" values used
# in Stage 1 of the thesis. Backs up existing YAMLs to /tmp/ first.
#
# Reverse with:
#   cp /tmp/deployment-web.bak.yaml deployment/deployment-web.yaml
#   cp /tmp/deployment-jobs.bak.yaml deployment/deployment-jobs.yaml
#
# Usage:
#   bash testing/patch-resources-to-naive.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB="$REPO_ROOT/deployment/deployment-web.yaml"
JOBS="$REPO_ROOT/deployment/deployment-jobs.yaml"

ts="$(date +%Y%m%d-%H%M%S)"
cp "$WEB"  "/tmp/deployment-web.bak.${ts}.yaml"
cp "$JOBS" "/tmp/deployment-jobs.bak.${ts}.yaml"
echo "Backups → /tmp/deployment-{web,jobs}.bak.${ts}.yaml"

# Use Python to rewrite the resources block — sed across multi-line blocks
# with embedded comments is fragile. Python regex with re.DOTALL is robust:
# we match `resources:` plus everything up to the next sibling key
# (`command:` for both deployments) and replace with a clean block.
python3 <<'PY'
import re, pathlib, sys

def patch(path, web_or_jobs):
    text = pathlib.Path(path).read_text()
    if web_or_jobs == 'web':
        new_block = """          resources:
            requests:
              cpu: 800m       # NAIVE — pre-VPA engineer default for Stage 1 baseline
              memory: 1Gi     # NAIVE
            limits:
              cpu: "2"        # NAIVE
              memory: 3Gi     # NAIVE
"""
    else:
        new_block = """          resources:
            requests:
              cpu: 500m       # NAIVE — pre-VPA engineer default for Stage 1 baseline
              memory: 1Gi     # NAIVE
            limits:
              cpu: "2"        # NAIVE
              memory: 3Gi     # NAIVE
"""
    # Match the existing resources block — from `          resources:` (10
    # spaces of indent in this repo) up to the next sibling key (command, ports,
    # volumeMounts, env etc) at the same indent. We anchor on the next key
    # being at exactly 10 spaces indent + `\w`.
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
echo "Done. Apply with:  ./deploy.sh baseline"
