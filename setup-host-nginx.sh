#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NGINX_CONF_SOURCE="$SCRIPT_DIR/host-nginx-canvas.conf"
NGINX_SITE_NAME="canvas.io.vn"
NGINX_CONF_TARGET="/etc/nginx/sites-available/$NGINX_SITE_NAME"
NGINX_ENABLED_TARGET="/etc/nginx/sites-enabled/$NGINX_SITE_NAME"

if [[ ! -f "$NGINX_CONF_SOURCE" ]]; then
  echo "Missing nginx config: $NGINX_CONF_SOURCE"
  exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
  echo "Installing nginx..."
  sudo apt update
  sudo apt install -y nginx
fi

echo "Copying nginx site config..."
sudo cp "$NGINX_CONF_SOURCE" "$NGINX_CONF_TARGET"

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
echo "Verify Canvas with: curl -H 'Host: canvas.io.vn' http://127.0.0.1"
