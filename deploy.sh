#!/bin/bash
set -e

MODE="${1:-migrate}"

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
kubectl apply -f ingress/

kubectl rollout status deployment/canvas-web -n canvas --timeout=300s
kubectl rollout status deployment/canvas-jobs -n canvas --timeout=300s

echo "Deployment completed with mode: $MODE"
