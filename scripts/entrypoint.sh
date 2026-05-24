#!/bin/bash
# Container entrypoint.
# Idempotent: safe to run on every restart.
#  1. Generate self-signed CA + server cert if not already on the ssl volume.
#  2. Create the lab users defined in users.env (if missing).
#  3. Optionally set up NAT for the 192.168.99.0/24 client pool.
#  4. Exec ocserv in foreground.

set -euo pipefail

# admin-panel denylist file (managed by the admin service via shared volume)
mkdir -p /etc/ocserv/control
[ -f /etc/ocserv/control/disabled-users ] || : > /etc/ocserv/control/disabled-users
chmod 644 /etc/ocserv/control/disabled-users

SSL_DIR=/etc/ocserv/ssl
CA_KEY="$SSL_DIR/ca-key.pem"
CA_CERT="$SSL_DIR/ca-cert.pem"
SRV_KEY="$SSL_DIR/server-key.pem"
SRV_CERT="$SSL_DIR/server-cert.pem"

log() { echo "[entrypoint] $*"; }

# ---------------------------------------------------------------------------
# 1. Certs. Generated once, persisted on the `ocserv-ssl` volume.
# ---------------------------------------------------------------------------
mkdir -p "$SSL_DIR"

if [[ ! -f "$SRV_CERT" || ! -f "$SRV_KEY" ]]; then
    log "no server cert found — generating self-signed CA + server cert via certtool"

    # CA
    certtool --generate-privkey --outfile "$CA_KEY" 2>/dev/null
    cat > /tmp/ca.tmpl <<EOF
cn = "ocserv-lab CA"
organization = "ocserv-lab"
serial = 1
expiration_days = 3650
ca
signing_key
cert_signing_key
crl_signing_key
EOF
    certtool --generate-self-signed --load-privkey "$CA_KEY" \
             --template /tmp/ca.tmpl --outfile "$CA_CERT" 2>/dev/null

    # Server cert signed by the CA
    certtool --generate-privkey --outfile "$SRV_KEY" 2>/dev/null
    cat > /tmp/srv.tmpl <<EOF
cn = "vpn.local"
organization = "ocserv-lab"
expiration_days = 3650
dns_name = "vpn.local"
dns_name = "localhost"
ip_address = "127.0.0.1"
signing_key
encryption_key
tls_www_server
EOF
    certtool --generate-certificate --load-privkey "$SRV_KEY" \
             --load-ca-certificate "$CA_CERT" --load-ca-privkey "$CA_KEY" \
             --template /tmp/srv.tmpl --outfile "$SRV_CERT" 2>/dev/null

    rm -f /tmp/ca.tmpl /tmp/srv.tmpl
    chmod 600 "$CA_KEY" "$SRV_KEY"
    log "certificates ready"
else
    log "certificates already present on ssl volume — reusing"
fi

# ---------------------------------------------------------------------------
# 2. Lab users from users.env.
# Format:   LAB_USERS="alice:alice-pass-123 bob:bob-pass-123"
# Space-separated entries, colon-separated user:password.
# Plaintext is intentional — this is a throwaway test stand. Don't do this in
# prod (commented in users.env itself).
# ---------------------------------------------------------------------------
if [[ -z "${LAB_USERS:-}" ]]; then
    log "ERROR: LAB_USERS not set (check users.env in compose)" >&2
    exit 1
fi

for entry in $LAB_USERS; do
    user="${entry%%:*}"
    pass="${entry#*:}"
    if [[ -z "$user" || -z "$pass" || "$user" == "$pass" ]]; then
        log "WARN: skipping malformed user entry '$entry'"
        continue
    fi

    if id "$user" &>/dev/null; then
        log "user '$user' already exists — refreshing password"
    else
        log "creating user '$user'"
        # /usr/sbin/nologin — VPN users don't need an interactive shell, but they
        # do need a real home for ~/.google_authenticator. -m creates it if not
        # already on the home volume.
        useradd --create-home --shell /usr/sbin/nologin "$user"
    fi
    echo "${user}:${pass}" | chpasswd
done

# ---------------------------------------------------------------------------
# 3. Optional NAT so traffic from the client pool gets masqueraded out.
# Best-effort: not all Docker Desktop installs allow iptables manipulation.
# If it fails, auth still works — only "internet through the tunnel" breaks,
# which on macOS Docker Desktop is largely out of reach anyway. See README.
# ---------------------------------------------------------------------------
if iptables -t nat -C POSTROUTING -s 192.168.99.0/24 -j MASQUERADE &>/dev/null; then
    log "NAT rule already in place"
elif iptables -t nat -A POSTROUTING -s 192.168.99.0/24 -j MASQUERADE 2>/dev/null; then
    log "NAT rule installed (POSTROUTING masquerade for 192.168.99.0/24)"
else
    log "NAT setup skipped — iptables not permitted (auth will still work)"
fi

# ---------------------------------------------------------------------------
# 4. Start ocserv. Foreground so Docker can supervise it.
# ---------------------------------------------------------------------------
log "starting ocserv on tcp/udp 443 (mapped to host 4443)"
# `-d 3` raises log verbosity so PAM auth events are visible in `docker logs`.
# This is fine for a lab; in prod you'd keep this off and use syslog.
exec ocserv --foreground --debug=3 --config /etc/ocserv/ocserv.conf
