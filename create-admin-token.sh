#!/bin/bash
set -euo pipefail

K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"
ADMIN_LOGIN="${ADMIN_LOGIN:-admin@canvas.local}"

if [[ -z "${KUBECONFIG:-}" && -f "$K3S_KUBECONFIG" ]]; then
  export KUBECONFIG="$K3S_KUBECONFIG"
fi

echo "Using kubeconfig: ${KUBECONFIG:-default}"
kubectl get pods -n canvas >/dev/null

kubectl exec -i -n canvas deployment/canvas-web -- bash -lc "bundle exec rails runner '
pseud = Pseudonym.find_by!(unique_id: \"$ADMIN_LOGIN\")
token = AccessToken.create!(
  user: pseud.user,
  developer_key: DeveloperKey.default,
  purpose: \"postman\"
)
token.activate! if token.pending?
puts token.full_token
'"
