#!/bin/bash
set -e

kubectl apply -f namespace.yaml

kubectl apply -f secret.yaml
kubectl apply -f config/

kubectl apply -f postgres.yaml
kubectl apply -f redis.yaml

kubectl apply -f pvc/

kubectl apply -f deployment/
kubectl apply -f service/
kubectl apply -f ingress/

echo "⏳ Waiting for DB..."
sleep 20

kubectl apply -f job-db-setup.yaml

echo "✅ Deployment completed"