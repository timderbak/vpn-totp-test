# ocserv + TOTP lab — Debian-based, targeted at linux/arm64 (Apple Silicon).
# Pin platform explicitly so `docker compose build` doesn't try to QEMU-emulate amd64.
FROM --platform=linux/arm64 debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Packages:
#  - ocserv:                       the OpenConnect VPN server itself
#  - libpam-google-authenticator:  PAM module that validates 6-digit TOTP codes
#  - gnutls-bin:                   `certtool` for generating the self-signed CA + server cert
#  - iproute2:                     `ip` command (ocserv uses it to bring up the tun device)
#  - iptables:                     for optional NAT/MASQUERADE inside the container
#  - qrencode:                     used by `totp-enroll` to print the QR code in the terminal
#  - ca-certificates, procps:      sanity baseline
RUN apt-get update && apt-get install -y --no-install-recommends \
        ocserv \
        libpam-google-authenticator \
        libpam-ldapd \
        nslcd \
        libnss-ldapd \
        ldap-utils \
        rsyslog \
        pamtester \
        gettext-base \
        gnutls-bin \
        iproute2 \
        iptables \
        qrencode \
        ca-certificates \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Strip the distro default PAM config — we override it below.
RUN rm -f /etc/pam.d/ocserv

# Tell NSS to consult ldap (via nslcd) for passwd/group/shadow lookups.
# Without this, `getent passwd alice` returns nothing and PAM can't resolve
# the user even after a successful pam_ldap bind.
RUN sed -ri 's/^(passwd|group|shadow):\s+files\s*$/\1:         files ldap/' /etc/nsswitch.conf

# Configs and scripts go in.
COPY config/ocserv.conf /etc/ocserv/ocserv.conf
COPY pam/ocserv         /etc/pam.d/ocserv
COPY scripts/entrypoint.sh   /usr/local/bin/entrypoint
COPY scripts/totp-enroll     /usr/local/bin/totp-enroll
RUN chmod +x /usr/local/bin/entrypoint /usr/local/bin/totp-enroll

# Where generated CA and server cert/key will live.
# entrypoint.sh creates them on first start if missing.
RUN mkdir -p /etc/ocserv/ssl

EXPOSE 443/tcp 443/udp

ENTRYPOINT ["/usr/local/bin/entrypoint"]
