#!/bin/bash
set -euo pipefail

MODE="${1:-migrate}"
K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"

if [[ -z "${KUBECONFIG:-}" && -f "$K3S_KUBECONFIG" ]]; then
  export KUBECONFIG="$K3S_KUBECONFIG"
fi

if ! kubectl version --client >/dev/null 2>&1; then
  echo "kubectl is required but not installed."
  exit 1
fi

echo "Using kubeconfig: ${KUBECONFIG:-default}"
kubectl get nodes >/dev/null

kubectl apply -f namespace.yaml

kubectl apply -f secret.yaml
kubectl apply -f config/

kubectl apply -f postgres.yaml
kubectl apply -f redis.yaml
kubectl apply -f pvc/

echo "Waiting for Postgres deployment to become ready..."
kubectl rollout status deployment/postgres -n canvas --timeout=300s

case "$MODE" in
  bootstrap)
    kubectl delete job canvas-db-bootstrap -n canvas --ignore-not-found
    kubectl apply -f job-db-bootstrap.yaml
    kubectl wait --for=condition=complete job/canvas-db-bootstrap -n canvas --timeout=1800s
    ;;
  migrate)
    kubectl delete job canvas-db-migrate -n canvas --ignore-not-found
    kubectl apply -f job-db-migrate.yaml
    kubectl wait --for=condition=complete job/canvas-db-migrate -n canvas --timeout=1800s
    ;;
  *)
    echo "Usage: ./deploy.sh [bootstrap|migrate]"
    exit 1
    ;;
esac

kubectl apply -f deployment/
kubectl apply -f service/

kubectl rollout status deployment/canvas-web -n canvas --timeout=300s
kubectl rollout status deployment/canvas-jobs -n canvas --timeout=300s

echo "Deployment completed with mode: $MODE"
echo "Canvas service URL: http://canvas.io.vn"
