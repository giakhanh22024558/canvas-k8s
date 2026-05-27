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
    DB_MODE="migrate"
    SCALING_MODE="prescaled"
    ;;
  *)
    echo "Usage: ./deploy.sh [baseline|hpa|prescaled|bootstrap|migrate]"
    echo "  baseline:  migrate DB, 1 web + 1 jobs pod, no HPA"
    echo "  hpa:       migrate DB, deploy with HPA enabled (VPA resources)"
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

WEB_YAML="deployment/deployment-web.yaml"
JOBS_YAML="deployment/deployment-jobs.yaml"
echo "Using web deployment:  $WEB_YAML (VPA resources)"
echo "Using jobs deployment: $JOBS_YAML (VPA resources)"

kubectl apply -f "$WEB_YAML"

jobs_desired=$(grep '^\s*replicas:' "$JOBS_YAML" | awk '{print $2}' | head -1)
jobs_desired="${jobs_desired:-1}"
current_jobs=$(kubectl get deployment canvas-jobs -n canvas -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
if [[ "$current_jobs" -gt 0 ]]; then
  echo "Scaling canvas-jobs to 0 to avoid single-node rollout deadlock..."
  kubectl scale deployment canvas-jobs -n canvas --replicas=0
  kubectl wait --for=delete pod -l app=canvas-jobs -n canvas --timeout=120s || true
fi
kubectl apply -f "$JOBS_YAML"
if [[ "$jobs_desired" -gt 0 ]]; then
  echo "Scaling canvas-jobs back to $jobs_desired replicas..."
  kubectl scale deployment canvas-jobs -n canvas --replicas="$jobs_desired"
fi

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
    kubectl delete -f deployment/hpa.yaml --ignore-not-found
    web_replicas="${PRESCALED_WEB_REPLICAS:-5}"
    jobs_replicas="${PRESCALED_JOBS_REPLICAS:-3}"
    kubectl scale deployment/canvas-web  --replicas="$web_replicas" -n canvas
    kubectl scale deployment/canvas-jobs --replicas="$jobs_replicas" -n canvas
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
  echo "Replicas:         web=1-3, jobs=1-2 (HPA managed, VPA resources)"
fi
echo "Canvas service URL: http://canvas.io.vn"
