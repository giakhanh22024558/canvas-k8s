#!/bin/bash
# vpa-recommend.sh — VPA setup and recommendation reader for Canvas LMS
#
# Usage:
#   bash testing/vpa-recommend.sh setup   # install VPA recommender + apply manifests
#   bash testing/vpa-recommend.sh         # read current recommendations (default)
#
# Workflow:
#   1. bash testing/vpa-recommend.sh setup
#   2. TEST_TYPE=long-stress bash testing/run-load-test.sh
#   3. bash testing/vpa-recommend.sh
#      → shows data-driven request/limit values to put in deployment-web.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NAMESPACE="${NAMESPACE:-canvas}"
COMMAND="${1:-read}"

# ── helpers ────────────────────────────────────────────────────────────────────

die() { echo "ERROR: $*" >&2; exit 1; }

require_kubectl() {
  command -v kubectl >/dev/null 2>&1 || die "kubectl not found"
}

vpa_crds_installed() {
  kubectl get crd verticalpodautoscalers.autoscaling.k8s.io \
    >/dev/null 2>&1
}

hr() { printf '%0.s─' {1..60}; echo; }

# ── setup ──────────────────────────────────────────────────────────────────────

cmd_setup() {
  echo "=== VPA Setup ==="
  echo ""

  if vpa_crds_installed; then
    echo "✓ VPA CRDs already installed — skipping Helm install"
  else
    echo "▶ VPA CRDs not found. Installing via Helm..."

    if ! command -v helm >/dev/null 2>&1; then
      echo "  Helm not found. Installing Helm 3..."
      curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    fi

    helm repo add fairwinds-stable https://charts.fairwinds.com/stable 2>/dev/null || true
    helm repo update --fail-on-repo-update-fail 2>/dev/null || helm repo update

    # Install recommender only — admission controller and updater are not
    # needed for updateMode=Off (observe-only).
    helm install vpa fairwinds-stable/vpa \
      --namespace kube-system \
      --set admissionController.enabled=false \
      --set updater.enabled=false \
      --wait --timeout 120s

    echo "✓ VPA recommender installed"
  fi

  echo ""
  echo "▶ Applying VPA recommendation manifests..."
  kubectl apply -f "$REPO_ROOT/deployment/vpa-recommendation.yaml"

  echo ""
  echo "✓ VPA ready."
  echo ""
  echo "Next steps:"
  echo "  1. Run a load test:   TEST_TYPE=long-stress bash testing/run-load-test.sh"
  echo "  2. Read results:      bash testing/vpa-recommend.sh"
}

# ── read recommendations ───────────────────────────────────────────────────────

extract_json() {
  # Usage: extract_json <vpa-name> <containerIndex> <bound> <resource>
  # bound: lowerBound | target | upperBound
  # Memory values from VPA are raw bytes — convert to human-readable Mi/Gi.
  local vpa="$1" idx="$2" bound="$3" res="$4"
  kubectl get vpa "$vpa" -n "$NAMESPACE" -o json 2>/dev/null \
    | python3 -c "
import sys, json, re
try:
    data = json.load(sys.stdin)
    recs = data['status']['recommendation']['containerRecommendations']
    val = recs[$idx]['$bound'].get('$res', '?')
    # Convert raw byte integers to human-readable Mi/Gi
    if '$res' == 'memory' and isinstance(val, str) and re.match(r'^\d+$', val):
        b = int(val)
        mib = b / (1024 * 1024)
        if mib >= 1024:
            print(f'{mib/1024:.1f}Gi')
        else:
            print(f'{int(mib)}Mi')
    else:
        print(val)
except Exception:
    print('?')
"
}

current_resource() {
  # Usage: current_resource <deployment> <requests|limits> <cpu|memory>
  local dep="$1" kind="$2" res="$3"
  kubectl get deployment "$dep" -n "$NAMESPACE" \
    -o "jsonpath={.spec.template.spec.containers[0].resources.$kind.$res}" \
    2>/dev/null || echo "?"
}

recommendation_provided() {
  local vpa="$1"
  kubectl get vpa "$vpa" -n "$NAMESPACE" \
    -o jsonpath='{.status.conditions[?(@.type=="RecommendationProvided")].status}' \
    2>/dev/null || echo "False"
}

print_deployment_block() {
  local dep="$1" container_idx="$2" vpa="$3"

  local cur_cpu_req cur_cpu_lim cur_mem_req cur_mem_lim
  cur_cpu_req=$(current_resource "$dep" requests cpu)
  cur_cpu_lim=$(current_resource "$dep" limits cpu)
  cur_mem_req=$(current_resource "$dep" requests memory)
  cur_mem_lim=$(current_resource "$dep" limits memory)

  local provided
  provided=$(recommendation_provided "$vpa")

  printf "  %-22s  %-14s  %-14s\n" "" "CPU" "Memory"
  hr
  printf "  %-22s  %-14s  %-14s\n" "Current request" "$cur_cpu_req" "$cur_mem_req"
  printf "  %-22s  %-14s  %-14s\n" "Current limit" "$cur_cpu_lim" "$cur_mem_lim"

  if [[ "$provided" != "True" ]]; then
    echo ""
    echo "  ⏳ VPA has no recommendation yet."
    echo "     Run a load test first, then wait ~8 minutes for the recommender"
    echo "     to collect enough data."
    echo ""
    return
  fi

  local lo_cpu tgt_cpu up_cpu lo_mem tgt_mem up_mem
  lo_cpu=$(extract_json "$vpa"  "$container_idx" lowerBound cpu)
  tgt_cpu=$(extract_json "$vpa" "$container_idx" target     cpu)
  up_cpu=$(extract_json "$vpa"  "$container_idx" upperBound cpu)
  lo_mem=$(extract_json "$vpa"  "$container_idx" lowerBound memory)
  tgt_mem=$(extract_json "$vpa" "$container_idx" target     memory)
  up_mem=$(extract_json "$vpa"  "$container_idx" upperBound memory)

  hr
  printf "  %-22s  %-14s  %-14s\n" "VPA lower bound" "$lo_cpu" "$lo_mem"
  printf "  %-22s  %-14s  %-14s\n" "VPA target" "$tgt_cpu" "$tgt_mem"
  printf "  %-22s  %-14s  %-14s\n" "VPA upper bound" "$up_cpu" "$up_mem"
  echo ""
  echo "  ✔ Suggested values for deployment YAML:"
  echo "      resources:"
  echo "        requests:"
  printf "          cpu:    %s   # VPA target\n" "$tgt_cpu"
  printf "          memory: %s   # VPA target\n" "$tgt_mem"
  echo "        limits:"
  printf "          cpu:    %s   # VPA upper bound\n" "$up_cpu"
  printf "          memory: %s   # VPA upper bound\n" "$up_mem"
  echo ""
}

cmd_read() {
  require_kubectl

  if ! vpa_crds_installed; then
    die "VPA CRDs not installed. Run: bash testing/vpa-recommend.sh setup"
  fi

  if ! kubectl get vpa -n "$NAMESPACE" --no-headers 2>/dev/null | grep -q .; then
    die "No VPA objects found in namespace '$NAMESPACE'. Run: bash testing/vpa-recommend.sh setup"
  fi

  echo "=== VPA Resource Recommendations (namespace: $NAMESPACE) ==="
  echo ""

  echo "▶ canvas-web"
  print_deployment_block "canvas-web" 0 "canvas-web-vpa"

  echo "▶ canvas-jobs"
  print_deployment_block "canvas-jobs" 0 "canvas-jobs-vpa"

  echo "Apply changes:"
  echo "  1. Edit deployment/deployment-web.yaml with the values above"
  echo "  2. kubectl apply -f deployment/deployment-web.yaml"
  echo "  3. kubectl rollout status deployment/canvas-web -n canvas"
}

# ── dispatch ───────────────────────────────────────────────────────────────────

require_kubectl

case "$COMMAND" in
  setup)  cmd_setup ;;
  read|*) cmd_read  ;;
esac
