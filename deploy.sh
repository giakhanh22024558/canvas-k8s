#!/bin/bash
set -euo pipefail

MODE="${1:-hpa}"
K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"
DB_MODE="migrate"
SCALING_MODE="hpa"
BASELINE_DISABLE_JOBS="${BASELINE_DISABLE_JOBS:-false}"

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
  prescaled)
    # Fixed at HPA-maximum replicas (web=5, jobs=3) with no autoscaler.
    # Use this to isolate whether HPA's benefit comes from auto-scaling
    # behaviour or simply from having more pods available.
    DB_MODE="migrate"
    SCALING_MODE="prescaled"
    ;;
  *)
    echo "Usage: ./deploy.sh [baseline|hpa|prescaled|bootstrap|migrate]"
    echo "  baseline:  migrate DB, 1 web + 1 jobs pod, no HPA"
    echo "  hpa:       migrate DB, deploy with HPAs enabled (1-5 web, 1-3 jobs)"
    echo "  prescaled: migrate DB, fixed 5 web + 3 jobs pods, no HPA"
    echo "  bootstrap: initialize DB, deploy with HPAs enabled"
    echo "  migrate:   alias for hpa"
    echo "Environment:"
    echo "  BASELINE_DISABLE_JOBS=true  Scale canvas-jobs to 0 in baseline mode"
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
    if [[ "$BASELINE_DISABLE_JOBS" == "true" ]]; then
      kubectl scale deployment/canvas-jobs --replicas=0 -n canvas
    else
      kubectl scale deployment/canvas-jobs --replicas=1 -n canvas
    fi
    ;;
  hpa)
    kubectl apply -f deployment/hpa.yaml
    ;;
  prescaled)
    # Remove HPA so Kubernetes cannot override the replica counts
    kubectl delete -f deployment/hpa.yaml --ignore-not-found
    # Match HPA upper limits: web max=5, jobs max=3
    kubectl scale deployment/canvas-web  --replicas=5 -n canvas
    kubectl scale deployment/canvas-jobs --replicas=3 -n canvas
    ;;
esac

kubectl apply -f service/

kubectl rollout status deployment/canvas-web -n canvas --timeout=300s
if ! [[ "$SCALING_MODE" == "baseline" && "$BASELINE_DISABLE_JOBS" == "true" ]]; then
  kubectl rollout status deployment/canvas-jobs -n canvas --timeout=300s
fi

if [[ "$SCALING_MODE" == "hpa" ]]; then
  kubectl get hpa -n canvas
else
  echo "HPAs removed — fixed replica counts in effect."
fi

echo ""
echo "Current pod state:"
kubectl get pods -n canvas

echo ""
echo "Deployment completed with mode: $MODE"
echo "Database action:  $DB_MODE"
echo "Scaling mode:     $SCALING_MODE"
if [[ "$SCALING_MODE" == "baseline" ]]; then
  echo "Replicas:         web=1, jobs=1 (fixed)"
elif [[ "$SCALING_MODE" == "prescaled" ]]; then
  echo "Replicas:         web=5, jobs=3 (fixed, no HPA)"
elif [[ "$SCALING_MODE" == "hpa" ]]; then
  echo "Replicas:         web=1-5, jobs=1-3 (HPA managed)"
fi
echo "Canvas service URL: http://canvas.io.vn"
