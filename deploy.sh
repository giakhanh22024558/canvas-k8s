#!/bin/bash
set -euo pipefail

MODE="${1:-hpa}"
K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"
DB_MODE="migrate"
SCALING_MODE="hpa"

case "$MODE" in
  bootstrap)
    DB_MODE="bootstrap"
    SCALING_MODE="hpa"
    ;;
  migrate)
    DB_MODE="migrate"
    SCALING_MODE="hpa"
    ;;
  baseline)
    DB_MODE="migrate"
    SCALING_MODE="baseline"
    ;;
  hpa)
    DB_MODE="migrate"
    SCALING_MODE="hpa"
    ;;
  *)
    echo "Usage: ./deploy.sh [baseline|hpa|bootstrap|migrate]"
    echo "  baseline: migrate DB, deploy fixed replicas, remove HPAs"
    echo "  hpa:      migrate DB, deploy with HPAs enabled"
    echo "  bootstrap: initialize DB, deploy with HPAs enabled"
    echo "  migrate:   alias for hpa"
    exit 1
    ;;
esac

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

case "$DB_MODE" in
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
esac

kubectl apply -f deployment/deployment-web.yaml
kubectl apply -f deployment/deployment-jobs.yaml

case "$SCALING_MODE" in
  baseline)
    kubectl delete -f deployment/hpa.yaml --ignore-not-found
    kubectl scale deployment/canvas-web --replicas=1 -n canvas
    kubectl scale deployment/canvas-jobs --replicas=1 -n canvas
    ;;
  hpa)
    kubectl apply -f deployment/hpa.yaml
    ;;
esac

kubectl apply -f service/

kubectl rollout status deployment/canvas-web -n canvas --timeout=300s
kubectl rollout status deployment/canvas-jobs -n canvas --timeout=300s

if [[ "$SCALING_MODE" == "hpa" ]]; then
  kubectl get hpa -n canvas
else
  echo "HPAs removed for baseline mode."
fi

echo "Deployment completed with mode: $MODE"
echo "Database action: $DB_MODE"
echo "Scaling mode: $SCALING_MODE"
echo "Canvas service URL: http://canvas.io.vn"
