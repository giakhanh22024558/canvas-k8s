#!/bin/bash
set -euo pipefail

K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"

echo "Starting k3s..."
sudo systemctl start k3s

echo "Waiting for k3s API..."
for _ in $(seq 1 30); do
  if sudo test -f "$K3S_KUBECONFIG"; then
    break
  fi
  sleep 2
done

if [[ ! -f "$K3S_KUBECONFIG" ]]; then
  echo "k3s kubeconfig not found at $K3S_KUBECONFIG"
  exit 1
fi

sudo chmod 644 "$K3S_KUBECONFIG"
export KUBECONFIG="$K3S_KUBECONFIG"

for _ in $(seq 1 60); do
  if kubectl get nodes >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "Cluster status:"
kubectl get nodes

if kubectl get namespace canvas >/dev/null 2>&1; then
  echo
  echo "Canvas namespace resources:"
  kubectl get all -n canvas
fi

echo
echo "KUBECONFIG=$KUBECONFIG"
echo "Canvas URL: http://canvas.io.vn:30080"
