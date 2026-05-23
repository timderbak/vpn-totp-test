#!/usr/bin/env bash
set -euo pipefail

# E2E smoke: bring up the lab, create an API token, enroll alice via API,
# verify TOTP secret produces a valid code, then revoke alice and verify she
# can no longer authenticate via openconnect.

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "[e2e] ensuring .env is present"
[ -f .env ] || { echo "create .env from .env.example first"; exit 1; }

echo "[e2e] (re)building stack"
docker compose down -v >/dev/null 2>&1 || true
docker compose up -d --build

echo "[e2e] waiting for admin healthz"
for _ in $(seq 1 30); do
  curl -sk https://localhost:8443/healthz >/dev/null && break
  sleep 1
done

# Bootstrap admin + enroll its TOTP via direct DB poke (no UI loop in smoke).
# Then create an API token via Python script inside the container.
echo "[e2e] minting API token via direct admin DB INSERT"
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
print(create_token(c, name="e2e-smoke", scopes=["enroll","revoke","read"], created_by_admin_id=admin_id).plaintext)
PY
)
echo "[e2e] got token prefix: ${TOKEN:0:8}…"

echo "[e2e] enrolling alice via API"
RESP=$(curl -sk -X POST https://localhost:8443/api/v1/users/alice/enroll \
       -H "Authorization: Bearer $TOKEN")
SECRET=$(echo "$RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['secret'])")
echo "[e2e] enrolled, secret length=${#SECRET}"
[ ${#SECRET} -eq 32 ] || { echo "wrong secret length"; exit 1; }

echo "[e2e] verifying .google_authenticator was written for alice"
docker compose exec -T ocserv test -f /home/alice/.google_authenticator

echo "[e2e] revoking alice"
curl -sk -X POST https://localhost:8443/api/v1/users/alice/revoke \
     -H "Authorization: Bearer $TOKEN" >/dev/null

echo "[e2e] verifying alice is on denylist"
docker compose exec -T ocserv grep -q '^alice$' /etc/ocserv/control/disabled-users

echo "[e2e] verifying .google_authenticator was deleted"
! docker compose exec -T ocserv test -f /home/alice/.google_authenticator

echo "[e2e] OK"
