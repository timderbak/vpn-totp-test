#!/usr/bin/env bash
set -euo pipefail

SSL_DIR="/etc/admin/ssl"
CERT="$SSL_DIR/server.crt"
KEY="$SSL_DIR/server.key"

mkdir -p "$SSL_DIR"

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  echo "[admin] generating self-signed TLS cert ..."
  openssl req -x509 -newkey rsa:2048 -nodes \
      -keyout "$KEY" -out "$CERT" -days 825 \
      -subj "/CN=ocserv-admin" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
  chmod 600 "$KEY"
fi

mkdir -p /var/lib/admin

echo "[admin] starting uvicorn on 0.0.0.0:8443 (HTTPS)"
exec uvicorn app.main:app \
    --host 0.0.0.0 --port 8443 \
    --ssl-keyfile "$KEY" --ssl-certfile "$CERT" \
    --access-log
