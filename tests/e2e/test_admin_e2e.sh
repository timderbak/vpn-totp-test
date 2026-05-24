#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

[ -f .env ] || { echo "create .env from .env.example first"; exit 1; }

echo "[e2e] (re)building stack"
docker compose down -v >/dev/null 2>&1 || true
docker compose up -d --build

echo "[e2e] waiting for ldap healthy"
for _ in $(seq 1 30); do
  status=$(docker inspect --format '{{.State.Health.Status}}' ocserv-ldap 2>/dev/null || echo none)
  [ "$status" = "healthy" ] && break
  sleep 2
done

echo "[e2e] waiting for admin healthz"
for _ in $(seq 1 30); do
  docker compose exec -T admin python -c "
import urllib.request, ssl
ctx = ssl._create_unverified_context()
print(urllib.request.urlopen('https://localhost:8443/healthz', context=ctx).read())
" >/dev/null 2>&1 && break
  sleep 1
done

echo "[e2e] minting API token"
TOKEN=$(docker compose exec -T admin python - <<'PY'
from app.db import init_db, connect
from app.config import get_settings
from app.tokens import create_token
from app.auth import bootstrap_admin_if_needed
s = get_settings()
init_db(s.db_path)
c = connect(s.db_path)
bootstrap_admin_if_needed(c, username=s.bootstrap_username, password_hash=s.bootstrap_password_hash)
admin_id = c.execute("SELECT id FROM admins WHERE username=?", (s.bootstrap_username,)).fetchone()["id"]
print(create_token(c, name="e2e", scopes=["enroll","revoke","read"], created_by_admin_id=admin_id).plaintext)
PY
)

echo "[e2e] GET /api/v1/users (LDAP-backed)"
docker compose exec -T admin python - <<PY
import urllib.request, ssl, json
ctx = ssl._create_unverified_context()
req = urllib.request.Request("https://localhost:8443/api/v1/users",
                             headers={"Authorization": "Bearer $TOKEN"})
data = json.load(urllib.request.urlopen(req, context=ctx))
names = sorted(u["username"] for u in data)
assert names == ["alice", "bob"], f"expected alice/bob, got {names}"
print("LDAP list ok:", names)
PY

echo "[e2e] enroll alice via API"
docker compose exec -T admin python - <<PY
import urllib.request, ssl, json
ctx = ssl._create_unverified_context()
req = urllib.request.Request("https://localhost:8443/api/v1/users/alice/enroll",
                             method="POST",
                             headers={"Authorization": "Bearer $TOKEN"})
secret = json.load(urllib.request.urlopen(req, context=ctx))["secret"]
assert len(secret) == 32
print("enroll ok, secret length", len(secret))
PY

echo "[e2e] verify home + TOTP file in ocserv volume"
docker compose exec -T ocserv test -d /home/alice
docker compose exec -T ocserv test -f /home/alice/.google_authenticator
docker compose exec -T ocserv stat -c "%a %u:%g" /home/alice

echo "[e2e] revoke alice via API"
docker compose exec -T admin python - <<PY
import urllib.request, ssl
ctx = ssl._create_unverified_context()
req = urllib.request.Request("https://localhost:8443/api/v1/users/alice/revoke",
                             method="POST",
                             headers={"Authorization": "Bearer $TOKEN"})
urllib.request.urlopen(req, context=ctx).read()
PY

echo "[e2e] verify denylist + TOTP gone"
docker compose exec -T ocserv grep -q '^alice$' /etc/ocserv/control/disabled-users
! docker compose exec -T ocserv test -f /home/alice/.google_authenticator

echo "[e2e] OK"
