#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"

if [[ -z "${KUBECONFIG:-}" && -f "$K3S_KUBECONFIG" ]]; then
  export KUBECONFIG="$K3S_KUBECONFIG"
fi

echo "Using kubeconfig: ${KUBECONFIG:-default}"
kubectl get nodes >/dev/null

kubectl apply -f "$SCRIPT_DIR/monitoring/namespace.yaml"
kubectl apply -f "$SCRIPT_DIR/monitoring/prometheus-config.yaml"
kubectl apply -f "$SCRIPT_DIR/monitoring/cadvisor.yaml"
kubectl apply -f "$SCRIPT_DIR/monitoring/prometheus.yaml"

kubectl rollout status deployment/prometheus -n canvas-monitoring --timeout=300s
kubectl rollout status daemonset/cadvisor -n canvas-monitoring --timeout=300s

echo "Prometheus is available at http://127.0.0.1:30090"
