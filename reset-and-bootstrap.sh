#!/bin/bash
set -euo pipefail

K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"

if [[ -z "${KUBECONFIG:-}" && -f "$K3S_KUBECONFIG" ]]; then
  export KUBECONFIG="$K3S_KUBECONFIG"
fi

echo "Using kubeconfig: ${KUBECONFIG:-default}"
kubectl get nodes >/dev/null

echo "Deleting namespace canvas (if it exists)..."
kubectl delete namespace canvas --ignore-not-found --wait=true

echo "Running fresh bootstrap deployment..."
"$(dirname "$0")/deploy.sh" bootstrap
