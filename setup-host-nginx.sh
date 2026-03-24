#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NGINX_CONF_SOURCE="$SCRIPT_DIR/host-nginx-canvas.conf"
NGINX_SITE_NAME="canvas.io.vn"
NGINX_CONF_TARGET="/etc/nginx/sites-available/$NGINX_SITE_NAME"
NGINX_ENABLED_TARGET="/etc/nginx/sites-enabled/$NGINX_SITE_NAME"
UPSTREAM_HOST="${UPSTREAM_HOST:-}"
UPSTREAM_PORT="${UPSTREAM_PORT:-30080}"
TLS_CERT_PATH="${TLS_CERT_PATH:-/etc/letsencrypt/live/canvas.io.vn/fullchain.pem}"
TLS_KEY_PATH="${TLS_KEY_PATH:-/etc/letsencrypt/live/canvas.io.vn/privkey.pem}"

if [[ ! -f "$NGINX_CONF_SOURCE" ]]; then
  echo "Missing nginx config: $NGINX_CONF_SOURCE"
  exit 1
fi

if [[ -z "$UPSTREAM_HOST" ]]; then
  if command -v minikube >/dev/null 2>&1; then
    UPSTREAM_HOST="$(minikube ip)"
  else
    UPSTREAM_HOST="127.0.0.1"
  fi
fi

if [[ -z "$UPSTREAM_HOST" ]]; then
  echo "Could not determine upstream host."
  exit 1
fi

if [[ -z "$UPSTREAM_PORT" ]]; then
  echo "Could not determine upstream port."
  exit 1
fi

if [[ ! -f "$TLS_CERT_PATH" ]]; then
  echo "TLS certificate not found: $TLS_CERT_PATH"
  exit 1
fi

if [[ ! -f "$TLS_KEY_PATH" ]]; then
  echo "TLS private key not found: $TLS_KEY_PATH"
  exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
  echo "Installing nginx..."
  sudo apt update
  sudo apt install -y nginx
fi

echo "Rendering nginx site config for upstream: http://$UPSTREAM_HOST:$UPSTREAM_PORT"
sed \
  -e "s#__UPSTREAM_HOST__#$UPSTREAM_HOST#g" \
  -e "s#__UPSTREAM_PORT__#$UPSTREAM_PORT#g" \
  -e "s#__TLS_CERT_PATH__#$TLS_CERT_PATH#g" \
  -e "s#__TLS_KEY_PATH__#$TLS_KEY_PATH#g" \
  "$NGINX_CONF_SOURCE" | sudo tee "$NGINX_CONF_TARGET" >/dev/null

echo "Enabling site..."
sudo ln -sf "$NGINX_CONF_TARGET" "$NGINX_ENABLED_TARGET"

if [[ -e /etc/nginx/sites-enabled/default ]]; then
  echo "Disabling default nginx site..."
  sudo rm -f /etc/nginx/sites-enabled/default
fi

echo "Validating nginx configuration..."
sudo nginx -t

echo "Reloading nginx..."
sudo systemctl enable nginx
sudo systemctl reload nginx

echo "Host nginx setup complete."
echo "Current upstream: http://$UPSTREAM_HOST:$UPSTREAM_PORT"
echo "Current TLS certificate: $TLS_CERT_PATH"
echo "Verify Canvas with: curl -I https://canvas.io.vn"
