# LDAP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `users.env`-based local Linux users with OpenLDAP as the source of truth for VPN users. Members of `cn=vpn-users` group can VPN. Admin container reads LDAP for the user list (with cache) and creates home + TOTP file on Issue. Admins remain local in `admin.db`.

**Architecture:** New `ldap` docker-compose service (osixia/openldap) seeded with alice and bob in `cn=vpn-users` group. ocserv PAM stack switches `pam_unix` → `pam_ldap` (denylist + TOTP unchanged). Admin gets a new `ldap_client.py` module with 30s in-memory cache and graceful stale-cache fallback when LDAP is unavailable. `users.env` and `useradd`/`chsh` blocks are removed.

**Tech Stack:** osixia/openldap:1.5.0, libpam-ldap (Debian), Python `ldap3==2.9.1`, FastAPI + Jinja + htmx (existing).

**Reference spec:** `docs/superpowers/specs/2026-05-24-ldap-integration-design.md`

---

## File Structure

```
ldap/                                       # [NEW]
├── bootstrap/
│   ├── 01-base.ldif                        # ou=users, ou=groups
│   ├── 02-seed-users.ldif                  # alice, bob ({SSHA}123)
│   ├── 03-vpn-group.ldif                   # cn=vpn-users + memberUid
│   └── 04-readonly.ldif                    # cn=admin-readonly
└── README.md                               # how to add a user

ocserv-ldap/                                # [NEW]
└── ldap.conf.tmpl                          # envsubst template for pam_ldap

admin/app/
├── ldap_client.py                          # [NEW] cached ldap3 client
├── config.py                               # +ADMIN_LDAP_* fields
├── users.py                                # list/enroll/revoke through LDAP
├── routes_web.py                           # +/users/_list, +/users/_refresh
├── routes_api.py                           # catch LdapUnavailable → 503
└── templates/
    ├── dashboard.html                      # skeleton + indicator + Refresh + ldap_error banner
    └── _users_table.html                   # [NEW] htmx tbody fragment

admin/tests/
├── test_ldap_client.py                     # [NEW]
└── test_ldap_failure.py                    # [NEW]
# + additions in test_users.py, test_routes_web.py, test_routes_api.py

# MODIFIED:
docker-compose.yml                          # +ldap service, +volumes, +ocserv-ldap mount
Dockerfile                                  # +libpam-ldap, +ldap-utils, +gettext-base
pam/ocserv                                  # pam_unix → pam_ldap
scripts/entrypoint.sh                       # -useradd, +wait-for-ldap, +envsubst
README.md                                   # +LDAP section
.env.example                                # +ADMIN_LDAP_*
admin/requirements.txt                      # +ldap3==2.9.1

# DELETED:
users.env                                   # users live in LDAP now
```

## Conventions

- Tests run in admin container: `docker compose run --rm admin pytest ...`
- One commit per task (Conventional Commits).
- TDD where it fits (modules with logic). Infrastructure tasks (Dockerfile, compose, LDIF) have a verification step instead.
- LDAP unit tests use `ldap3.Connection(client_strategy=MOCK_SYNC)` — no real slapd.

---

## Task 1: LDAP service in docker-compose with seed-LDIFs

**Files:**
- Create: `ldap/bootstrap/01-base.ldif`
- Create: `ldap/bootstrap/02-seed-users.ldif`
- Create: `ldap/bootstrap/03-vpn-group.ldif`
- Create: `ldap/bootstrap/04-readonly.ldif`
- Create: `ldap/README.md`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Generate {SSHA} hashes for passwords**

Run once interactively (outputs `{SSHA}…` lines — copy them into the LDIFs in step 2/4):

```bash
# user password "123"
docker run --rm osixia/openldap:1.5.0 slappasswd -h '{SSHA}' -s 123
# bind password — pick a random one
ADMIN_RO_PW=$(openssl rand -hex 16)
docker run --rm osixia/openldap:1.5.0 slappasswd -h '{SSHA}' -s "$ADMIN_RO_PW"
# remember $ADMIN_RO_PW for .env (ADMIN_LDAP_BIND_PASSWORD)
```

- [ ] **Step 2: Write the four LDIFs**

`ldap/bootstrap/01-base.ldif`:
```ldif
dn: ou=users,dc=vpn,dc=local
objectClass: organizationalUnit
ou: users

dn: ou=groups,dc=vpn,dc=local
objectClass: organizationalUnit
ou: groups
```

`ldap/bootstrap/02-seed-users.ldif` (use the SSHA hash from step 1 — same hash works for both since pwd is the same):
```ldif
dn: uid=alice,ou=users,dc=vpn,dc=local
objectClass: inetOrgPerson
objectClass: posixAccount
uid: alice
cn: Alice Liddell
sn: Liddell
uidNumber: 2001
gidNumber: 2001
homeDirectory: /home/alice
loginShell: /bin/bash
mail: alice@vpn.local
userPassword: {SSHA}REPLACE_WITH_HASH_FROM_STEP_1

dn: uid=bob,ou=users,dc=vpn,dc=local
objectClass: inetOrgPerson
objectClass: posixAccount
uid: bob
cn: Bob Builder
sn: Builder
uidNumber: 2002
gidNumber: 2002
homeDirectory: /home/bob
loginShell: /bin/bash
mail: bob@vpn.local
userPassword: {SSHA}REPLACE_WITH_HASH_FROM_STEP_1
```

`ldap/bootstrap/03-vpn-group.ldif`:
```ldif
dn: cn=vpn-users,ou=groups,dc=vpn,dc=local
objectClass: posixGroup
cn: vpn-users
gidNumber: 3000
memberUid: alice
memberUid: bob
```

`ldap/bootstrap/04-readonly.ldif` (SSHA from step 1 for the bind password):
```ldif
dn: cn=admin-readonly,dc=vpn,dc=local
objectClass: simpleSecurityObject
objectClass: organizationalRole
cn: admin-readonly
description: Read-only service account for admin panel
userPassword: {SSHA}REPLACE_WITH_BIND_HASH_FROM_STEP_1
```

- [ ] **Step 3: Add LDAP service + volumes to docker-compose.yml**

Append the `ldap` service inside `services:` (before `admin`) and the two new volumes:

```yaml
  ldap:
    image: osixia/openldap:1.5.0
    container_name: ocserv-ldap
    hostname: ldap
    environment:
      LDAP_ORGANISATION: "VPN Lab"
      LDAP_DOMAIN: vpn.local
      LDAP_ADMIN_PASSWORD: ${LDAP_ADMIN_PASSWORD}
      LDAP_CONFIG_PASSWORD: ${LDAP_CONFIG_PASSWORD}
      LDAP_RFC2307BIS_SCHEMA: "false"
      LDAP_TLS: "false"
    volumes:
      - ldap-data:/var/lib/ldap
      - ldap-config:/etc/ldap/slapd.d
      - ./ldap/bootstrap:/container/service/slapd/assets/config/bootstrap/ldif/custom:ro
    healthcheck:
      test: ["CMD-SHELL", "ldapsearch -x -H ldap://localhost -b dc=vpn,dc=local -s base >/dev/null"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped
```

In `volumes:` at the bottom, add:
```yaml
  ldap-data:
  ldap-config:
```

- [ ] **Step 4: Update .env.example with LDAP vars**

Append to `.env.example`:
```env
# LDAP root admin (for manual ldapadd / ldapmodify only)
LDAP_ADMIN_PASSWORD=REPLACE_WITH_RANDOM
LDAP_CONFIG_PASSWORD=REPLACE_WITH_RANDOM

# LDAP service account used by the admin panel and ocserv PAM (read-only)
ADMIN_LDAP_URL=ldap://ldap:389
ADMIN_LDAP_BIND_DN=cn=admin-readonly,dc=vpn,dc=local
ADMIN_LDAP_BIND_PASSWORD=REPLACE_WITH_SAME_PLAINTEXT_AS_HASHED_IN_LDIF_04
ADMIN_LDAP_BASE_DN=dc=vpn,dc=local
ADMIN_LDAP_USER_OU=ou=users,dc=vpn,dc=local
ADMIN_LDAP_VPN_GROUP_DN=cn=vpn-users,ou=groups,dc=vpn,dc=local
ADMIN_LDAP_CACHE_TTL=30
ADMIN_LDAP_TIMEOUT=5
```

Also update `.env` (not committed) with the real values used in step 1.

- [ ] **Step 5: Write ldap/README.md**

`ldap/README.md`:
```markdown
# LDAP layout for the lab

Single OpenLDAP container in docker-compose. Seeded on first start
from `bootstrap/*.ldif` (alphabetical order).

## Layout

```
dc=vpn,dc=local
├── ou=users
│   ├── uid=alice    (uidNumber=2001, password "123")
│   └── uid=bob      (uidNumber=2002, password "123")
├── ou=groups
│   └── cn=vpn-users (memberUid alice, bob)
└── cn=admin-readonly  (service account for the admin panel)
```

## Add a new VPN user

```bash
docker compose exec ldap ldapadd -x -D cn=admin,dc=vpn,dc=local -w "$LDAP_ADMIN_PASSWORD" <<EOF
dn: uid=carol,ou=users,dc=vpn,dc=local
objectClass: inetOrgPerson
objectClass: posixAccount
uid: carol
cn: Carol
sn: Carol
uidNumber: 2003
gidNumber: 2003
homeDirectory: /home/carol
loginShell: /bin/bash
mail: carol@vpn.local
userPassword: $(docker compose exec -T ldap slappasswd -h '{SSHA}' -s carol-pass)
EOF

# add to vpn-users
docker compose exec ldap ldapmodify -x -D cn=admin,dc=vpn,dc=local -w "$LDAP_ADMIN_PASSWORD" <<EOF
dn: cn=vpn-users,ou=groups,dc=vpn,dc=local
changetype: modify
add: memberUid
memberUid: carol
EOF
```

In the admin panel: dashboard → Issue → carol now has TOTP. She can connect via VPN.
```

- [ ] **Step 6: Verify LDAP starts and seed loaded**

```bash
# update .env with real values from step 1 first
docker compose up -d ldap
sleep 6  # give the bootstrap LDIFs time to load
docker compose exec ldap ldapsearch -x -b dc=vpn,dc=local -D cn=admin,dc=vpn,dc=local -w "$LDAP_ADMIN_PASSWORD" "(uid=alice)" uid uidNumber
```

Expected: returns an entry with `uid: alice` and `uidNumber: 2001`.

Verify group:
```bash
docker compose exec ldap ldapsearch -x -b ou=groups,dc=vpn,dc=local -D cn=admin,dc=vpn,dc=local -w "$LDAP_ADMIN_PASSWORD" "(cn=vpn-users)" memberUid
```
Expected: `memberUid: alice` and `memberUid: bob`.

Verify the readonly account can bind and search:
```bash
docker compose exec ldap ldapsearch -x -H ldap://localhost \
    -D cn=admin-readonly,dc=vpn,dc=local -w "$ADMIN_LDAP_BIND_PASSWORD" \
    -b ou=users,dc=vpn,dc=local "(objectClass=posixAccount)" uid
```
Expected: alice and bob returned.

- [ ] **Step 7: Commit**

```bash
git add ldap/ docker-compose.yml .env.example
git commit -m "feat(ldap): add OpenLDAP service with seed users and vpn-users group"
```

---

## Task 2: pam_ldap in ocserv + remove users.env

**Files:**
- Create: `ocserv-ldap/ldap.conf.tmpl`
- Modify: `Dockerfile`
- Modify: `pam/ocserv`
- Modify: `scripts/entrypoint.sh`
- Modify: `docker-compose.yml` (mount ocserv-ldap dir into ocserv container, env vars)
- Delete: `users.env`

- [ ] **Step 1: Create ocserv-ldap/ldap.conf.tmpl**

`ocserv-ldap/ldap.conf.tmpl`:
```
uri        ${ADMIN_LDAP_URL}
base       ${ADMIN_LDAP_BASE_DN}

binddn     ${ADMIN_LDAP_BIND_DN}
bindpw     ${ADMIN_LDAP_BIND_PASSWORD}

nss_base_passwd     ${ADMIN_LDAP_USER_OU}?one
nss_base_group      ou=groups,${ADMIN_LDAP_BASE_DN}?one

pam_groupdn         ${ADMIN_LDAP_VPN_GROUP_DN}
pam_member_attribute  memberUid

bind_timelimit      ${ADMIN_LDAP_TIMEOUT}
timelimit           ${ADMIN_LDAP_TIMEOUT}
network_timeout     ${ADMIN_LDAP_TIMEOUT}

ssl                 no
```

- [ ] **Step 2: Update ocserv Dockerfile**

Modify the `apt-get install` block in `Dockerfile` to add three packages:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        ocserv \
        libpam-google-authenticator \
        libpam-ldap \
        ldap-utils \
        gettext-base \
        gnutls-bin \
        iproute2 \
        iptables \
        qrencode \
        ca-certificates \
        procps \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Replace PAM stack**

Overwrite `pam/ocserv`:
```
# 1. Denylist managed by admin panel (shared volume ocserv-control).
auth requisite pam_listfile.so onerr=succeed item=user sense=deny \
     file=/etc/ocserv/control/disabled-users

# 2. Password via LDAP. pam_ldap reads /etc/ldap/ldap.conf.
auth required pam_ldap.so

# 3. Second factor — TOTP. File in /home/<u>/.google_authenticator.
auth required pam_google_authenticator.so debug

# Account-stage: pam_ldap enforces membership in pam_groupdn (vpn-users).
account required pam_ldap.so

# Session: ocserv never spawns a shell — pam_permit suffices.
session required pam_permit.so
```

- [ ] **Step 4: Update scripts/entrypoint.sh — remove users.env block, add wait-for-ldap and envsubst**

Open `scripts/entrypoint.sh`. Find the existing user-creation loop (look for `for entry in $LAB_USERS` and the surrounding `if id "$user"…chsh -s /bin/bash "$user"` block) and **delete the entire block** (from the `for entry in` header through its matching `done`).

Add the following block **after** the certificate generation block but **before** the line that finally launches ocserv (`exec ocserv …`):

```bash
# ---------------------------------------------------------------------------
# LDAP wait + ldap.conf render
# ---------------------------------------------------------------------------

# Render pam_ldap config from template (the template uses envsubst variables,
# so the bind password is never committed to git).
envsubst < /etc/ldap/ldap.conf.tmpl > /etc/ldap/ldap.conf
chmod 600 /etc/ldap/ldap.conf

# Wait until LDAP is reachable (5 tries × 2s) so the first VPN connect
# attempt doesn't race a cold LDAP container.
for i in 1 2 3 4 5; do
    if ldapsearch -x -H "$ADMIN_LDAP_URL" -b "$ADMIN_LDAP_BASE_DN" -s base \
            -D "$ADMIN_LDAP_BIND_DN" -w "$ADMIN_LDAP_BIND_PASSWORD" >/dev/null 2>&1; then
        log "LDAP reachable"
        break
    fi
    log "waiting for LDAP ($i/5)..."
    sleep 2
done
```

- [ ] **Step 5: Wire ocserv-ldap mount + env vars into docker-compose.yml**

Modify the `ocserv` service in `docker-compose.yml`:

```yaml
  ocserv:
    # ... existing fields ...
    env_file:
      - .env                                    # was: users.env
    volumes:
      - ocserv-ssl:/etc/ocserv/ssl
      - ocserv-home:/home
      - ocserv-control:/etc/ocserv/control
      - ./ocserv-ldap/ldap.conf.tmpl:/etc/ldap/ldap.conf.tmpl:ro
    depends_on:
      ldap:
        condition: service_healthy
```

Remove the line `env_file: - users.env`.

- [ ] **Step 6: Delete users.env**

```bash
git rm users.env
```

- [ ] **Step 7: Rebuild and verify ocserv container starts + ldap.conf rendered**

```bash
docker compose build ocserv
docker compose up -d --force-recreate ocserv
sleep 5
# verify pam config swapped
docker compose exec ocserv cat /etc/pam.d/ocserv | head -10
# verify ldap.conf rendered with real values (mind: file is mode 0600, read as root)
docker compose exec ocserv head -3 /etc/ldap/ldap.conf
# verify pam_ldap module is present
docker compose exec ocserv ls /lib/*/security/pam_ldap.so
```
Expected: pam stack starts with `pam_listfile`, ldap.conf has `uri ldap://ldap:389`, pam_ldap.so exists.

- [ ] **Step 8: Commit**

```bash
git add Dockerfile pam/ocserv scripts/entrypoint.sh docker-compose.yml ocserv-ldap/
git commit -m "feat(ocserv): switch PAM from pam_unix to pam_ldap, drop users.env"
```

---

## Task 3: Config + ldap_client module (TDD with MOCK_SYNC)

**Files:**
- Modify: `admin/requirements.txt`
- Modify: `admin/app/config.py`
- Create: `admin/app/ldap_client.py`
- Create: `admin/tests/test_ldap_client.py`

- [ ] **Step 1: Add ldap3 to requirements**

Append to `admin/requirements.txt`:
```
ldap3==2.9.1
```

Rebuild admin to pick up the dep:
```bash
docker compose build admin
```

- [ ] **Step 2: Add LDAP fields to Settings**

Append the LDAP fields to the `Settings` class in `admin/app/config.py`:

```python
class Settings(BaseSettings):
    # ... existing fields ...

    ldap_url: str = "ldap://ldap:389"
    ldap_bind_dn: str
    ldap_bind_password: str
    ldap_base_dn: str = "dc=vpn,dc=local"
    ldap_user_ou: str = "ou=users,dc=vpn,dc=local"
    ldap_vpn_group_dn: str = "cn=vpn-users,ou=groups,dc=vpn,dc=local"
    ldap_cache_ttl: int = 30
    ldap_timeout: int = 5
```

Run existing config tests to make sure nothing broke:
```bash
docker compose run --rm admin pytest tests/test_config.py -v
```
Expected: all PASS (existing tests use monkeypatch.setenv for required fields; new required fields will need to be set in the new ldap_client tests — that's fine).

- [ ] **Step 3: Write failing ldap_client tests**

`admin/tests/test_ldap_client.py`:
```python
"""Unit tests for ldap_client using ldap3 MOCK_SYNC.

MOCK_SYNC creates an in-memory LDAP server; no real slapd needed.
"""
import time
import pytest
from ldap3 import Connection, Server, MOCK_SYNC, OFFLINE_SLAPD_2_4
from app import ldap_client
from app.ldap_client import LdapUser, LdapUnavailable


@pytest.fixture
def mock_ldap(monkeypatch):
    """Build a mocked LDAP server seeded with alice, bob, vpn-users, and the bind acct."""
    server = Server("mock", get_info=OFFLINE_SLAPD_2_4)
    conn = Connection(server, user="cn=admin-readonly,dc=vpn,dc=local",
                      password="bindpw", client_strategy=MOCK_SYNC)
    # service account (so bind succeeds)
    conn.strategy.add_entry(
        "cn=admin-readonly,dc=vpn,dc=local",
        {"objectClass": ["simpleSecurityObject", "organizationalRole"],
         "cn": "admin-readonly", "userPassword": "bindpw"},
    )
    # OUs
    for ou in ("users", "groups"):
        conn.strategy.add_entry(
            f"ou={ou},dc=vpn,dc=local",
            {"objectClass": ["organizationalUnit"], "ou": ou},
        )
    # alice + bob
    for uid, uidnum in [("alice", 2001), ("bob", 2002)]:
        conn.strategy.add_entry(
            f"uid={uid},ou=users,dc=vpn,dc=local",
            {"objectClass": ["inetOrgPerson", "posixAccount"],
             "uid": uid, "cn": uid.capitalize(), "sn": uid.capitalize(),
             "uidNumber": uidnum, "gidNumber": uidnum,
             "homeDirectory": f"/home/{uid}", "loginShell": "/bin/bash",
             "mail": f"{uid}@vpn.local", "userPassword": "{SSHA}xxx"},
        )
    # carol — exists but NOT in vpn-users (used to test group filter)
    conn.strategy.add_entry(
        "uid=carol,ou=users,dc=vpn,dc=local",
        {"objectClass": ["inetOrgPerson", "posixAccount"],
         "uid": "carol", "cn": "Carol", "sn": "Carol",
         "uidNumber": 2003, "gidNumber": 2003,
         "homeDirectory": "/home/carol", "loginShell": "/bin/bash",
         "mail": "carol@vpn.local", "userPassword": "{SSHA}xxx"},
    )
    # group
    conn.strategy.add_entry(
        "cn=vpn-users,ou=groups,dc=vpn,dc=local",
        {"objectClass": ["posixGroup"], "cn": "vpn-users",
         "gidNumber": 3000, "memberUid": ["alice", "bob"]},
    )

    # Patch ldap_client's connection factory so production code uses our mock.
    def _factory(*args, **kwargs):
        c = Connection(server, user="cn=admin-readonly,dc=vpn,dc=local",
                       password="bindpw", client_strategy=MOCK_SYNC, auto_bind=True)
        # re-seed (each new connection in MOCK_SYNC starts empty)
        c.strategy.entries = conn.strategy.entries
        return c

    monkeypatch.setattr(ldap_client, "_connect", _factory)
    monkeypatch.setenv("ADMIN_LDAP_BIND_DN", "cn=admin-readonly,dc=vpn,dc=local")
    monkeypatch.setenv("ADMIN_LDAP_BIND_PASSWORD", "bindpw")
    ldap_client.invalidate_cache()
    yield


def test_list_users_returns_only_vpn_group_members(mock_ldap):
    users = ldap_client.list_users()
    names = sorted(u.username for u in users)
    assert names == ["alice", "bob"]      # carol not in vpn-users → excluded


def test_list_users_returns_uid_and_gid(mock_ldap):
    users = {u.username: u for u in ldap_client.list_users()}
    assert users["alice"].uid_number == 2001
    assert users["alice"].gid_number == 2001
    assert users["bob"].uid_number == 2002


def test_get_user_existing(mock_ldap):
    u = ldap_client.get_user("alice")
    assert u is not None
    assert u.uid_number == 2001
    assert u.email == "alice@vpn.local"


def test_get_user_unknown(mock_ldap):
    assert ldap_client.get_user("nobody") is None


def test_get_user_not_in_group_returns_none(mock_ldap):
    # carol exists in LDAP but is NOT in vpn-users → admin should not see her
    assert ldap_client.get_user("carol") is None


def test_cache_hit_skips_ldap(mock_ldap, monkeypatch):
    ldap_client.list_users()    # warm cache
    # break the factory — if cache works, second call doesn't touch it
    monkeypatch.setattr(ldap_client, "_connect",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("should not bind")))
    users = ldap_client.list_users()
    assert len(users) == 2


def test_invalidate_cache_forces_refetch(mock_ldap):
    ldap_client.list_users()
    ldap_client.invalidate_cache()
    assert ldap_client.cache_age_seconds() is None


def test_cache_age_seconds_after_fetch(mock_ldap):
    ldap_client.list_users()
    age = ldap_client.cache_age_seconds()
    assert age is not None and 0 <= age <= 2


def test_ldap_unavailable_when_bind_fails(monkeypatch):
    monkeypatch.setenv("ADMIN_LDAP_BIND_DN", "cn=admin-readonly,dc=vpn,dc=local")
    monkeypatch.setenv("ADMIN_LDAP_BIND_PASSWORD", "wrong")

    def _broken(*args, **kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr(ldap_client, "_connect", _broken)
    ldap_client.invalidate_cache()

    with pytest.raises(LdapUnavailable):
        ldap_client.list_users()
```

- [ ] **Step 4: Run tests, verify they FAIL (module doesn't exist yet)**

```bash
docker compose run --rm admin pytest tests/test_ldap_client.py -v
```
Expected: ImportError for `app.ldap_client`.

- [ ] **Step 5: Implement ldap_client.py**

`admin/app/ldap_client.py`:
```python
"""Cached LDAP client for the admin panel.

Reads members of cn=vpn-users using the read-only service account.
Caches the user list in-process for ADMIN_LDAP_CACHE_TTL seconds.
On LDAP failure (bind error, socket timeout) raises LdapUnavailable;
the last successful fetch is preserved in _stale_data for UX fallback.
"""
import time
from dataclasses import dataclass
from ldap3 import Connection, Server, ALL, SUBTREE
from ldap3.core.exceptions import LDAPException

from app.config import get_settings


@dataclass(frozen=True)
class LdapUser:
    username: str
    uid_number: int
    gid_number: int
    display_name: str | None
    email: str | None


class LdapUnavailable(Exception):
    """LDAP search failed (timeout, bind error, or socket-level error)."""


_cache: dict = {"data": None, "ts": 0.0}
_stale_data: list[LdapUser] | None = None


def _connect():
    """Default connection factory. Tests monkeypatch this."""
    s = get_settings()
    server = Server(s.ldap_url, get_info=ALL, connect_timeout=s.ldap_timeout)
    return Connection(server, user=s.ldap_bind_dn, password=s.ldap_bind_password,
                      auto_bind=True, receive_timeout=s.ldap_timeout)


def _member_uids(conn) -> list[str]:
    s = get_settings()
    conn.search(s.ldap_vpn_group_dn, "(objectClass=posixGroup)",
                search_scope=SUBTREE, attributes=["memberUid"])
    if not conn.entries:
        return []
    raw = conn.entries[0].memberUid.values if "memberUid" in conn.entries[0] else []
    return list(raw)


def _fetch_one(conn, uid: str) -> LdapUser | None:
    s = get_settings()
    conn.search(
        s.ldap_user_ou,
        f"(&(objectClass=posixAccount)(uid={uid}))",
        search_scope=SUBTREE,
        attributes=["uid", "uidNumber", "gidNumber", "cn", "mail"],
    )
    if not conn.entries:
        return None
    e = conn.entries[0]
    return LdapUser(
        username=str(e.uid.value),
        uid_number=int(e.uidNumber.value),
        gid_number=int(e.gidNumber.value),
        display_name=str(e.cn.value) if "cn" in e else None,
        email=str(e.mail.value) if "mail" in e and e.mail.value else None,
    )


def _fetch_all() -> list[LdapUser]:
    try:
        conn = _connect()
    except (LDAPException, OSError) as e:
        raise LdapUnavailable(str(e)) from e
    try:
        uids = _member_uids(conn)
        out = []
        for uid in uids:
            u = _fetch_one(conn, uid)
            if u is not None:
                out.append(u)
        return out
    except (LDAPException, OSError) as e:
        raise LdapUnavailable(str(e)) from e
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


def list_users() -> list[LdapUser]:
    """All members of vpn-users. Cached for ADMIN_LDAP_CACHE_TTL seconds."""
    global _stale_data
    s = get_settings()
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < s.ldap_cache_ttl:
        return _cache["data"]
    try:
        data = _fetch_all()
    except LdapUnavailable:
        raise
    _cache["data"] = data
    _cache["ts"] = now
    _stale_data = data
    return data


def get_user(username: str) -> LdapUser | None:
    """Single user by uid — only if they're in vpn-users."""
    for u in list_users():
        if u.username == username:
            return u
    return None


def invalidate_cache() -> None:
    _cache["data"] = None
    _cache["ts"] = 0.0


def cache_age_seconds() -> int | None:
    if _cache["data"] is None:
        return None
    return int(time.time() - _cache["ts"])


def stale_users() -> list[LdapUser] | None:
    """Last successful fetch; used for UX fallback when LDAP is down."""
    return _stale_data
```

- [ ] **Step 6: Run tests, verify PASS**

```bash
docker compose build admin
docker compose run --rm admin pytest tests/test_ldap_client.py -v
```
Expected: all 9 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add admin/requirements.txt admin/app/config.py admin/app/ldap_client.py admin/tests/test_ldap_client.py
git commit -m "feat(admin): add cached ldap3 client with vpn-users filter"
```

---

## Task 4: users.py — list from LDAP + ensure_home + enroll/revoke wiring

**Files:**
- Modify: `admin/app/users.py`
- Modify: `admin/tests/test_users.py`

- [ ] **Step 1: Write new failing tests in test_users.py**

Append to `admin/tests/test_users.py`:

```python
from unittest.mock import patch
from app.ldap_client import LdapUser


@pytest.fixture
def fake_ldap(monkeypatch):
    """Stub ldap_client.list_users / get_user to return a fixed pair (alice, bob)."""
    users = [
        LdapUser(username="alice", uid_number=2001, gid_number=2001,
                 display_name="Alice", email="alice@vpn.local"),
        LdapUser(username="bob", uid_number=2002, gid_number=2002,
                 display_name="Bob", email="bob@vpn.local"),
    ]
    monkeypatch.setattr("app.ldap_client.list_users", lambda: users)
    monkeypatch.setattr("app.ldap_client.get_user",
                        lambda uname: next((u for u in users if u.username == uname), None))
    monkeypatch.setattr("app.ldap_client.invalidate_cache", lambda: None)


def test_list_users_uses_ldap_not_filesystem(env, fake_ldap):
    """list_users no longer walks /home — comes from LDAP."""
    # remove home dirs created by fixture; LDAP-backed list should still return both
    import shutil
    shutil.rmtree(env["home"])
    Path(env["home"]).mkdir()
    users = list_users(env["home"], env["denylist"], env["conn"])
    names = sorted(u.username for u in users)
    assert names == ["alice", "bob"]


def test_enroll_user_creates_home_with_correct_uid(env, fake_ldap, tmp_path):
    import shutil
    shutil.rmtree(env["home"])
    Path(env["home"]).mkdir()
    enroll_user(env["home"], env["conn"], username="bob",
                actor_type="admin", actor_id=1, issuer="x")
    home = Path(env["home"]) / "bob"
    assert home.exists()
    assert (home / ".google_authenticator").exists()
    # mode 700 on the home dir
    assert oct(home.stat().st_mode)[-3:] == "700"


def test_enroll_user_for_unknown_ldap_uid_raises_not_found(env, fake_ldap):
    with pytest.raises(UserNotFound):
        enroll_user(env["home"], env["conn"], username="ghost",
                    actor_type="admin", actor_id=1, issuer="x")


def test_revoke_invalidates_ldap_cache(env, fake_ldap):
    calls = []
    import app.ldap_client as lc
    # re-monkeypatch invalidate_cache to capture
    pytest_monkey = pytest.MonkeyPatch()
    pytest_monkey.setattr(lc, "invalidate_cache", lambda: calls.append(1))
    try:
        revoke_user(env["home"], env["denylist"], env["conn"],
                    username="alice", actor_type="admin", actor_id=1)
    finally:
        pytest_monkey.undo()
    assert len(calls) >= 1
```

- [ ] **Step 2: Run tests, verify they FAIL**

```bash
docker compose run --rm admin pytest tests/test_users.py::test_list_users_uses_ldap_not_filesystem tests/test_users.py::test_enroll_user_creates_home_with_correct_uid tests/test_users.py::test_enroll_user_for_unknown_ldap_uid_raises_not_found tests/test_users.py::test_revoke_invalidates_ldap_cache -v
```
Expected: FAIL (current implementation reads `/home`, not LDAP; no `ensure_home`).

- [ ] **Step 3: Refactor users.py**

Open `admin/app/users.py`. Add at the top:
```python
from app import ldap_client
from app.ldap_client import LdapUser, LdapUnavailable
```

**Replace `list_users` body** with:
```python
def list_users(home_dir: str, denylist_path: str, conn: sqlite3.Connection) -> list[UserListEntry]:
    """List VPN users from LDAP, decorated with TOTP/denylist/journal state."""
    home = Path(home_dir)
    denied = set(_read_denylist(denylist_path))
    entries: list[UserListEntry] = []
    for ldap_user in ldap_client.list_users():
        name = ldap_user.username
        if not is_valid_username(name):
            continue
        ga = home / name / ".google_authenticator"
        last_row = conn.execute(
            "SELECT ts FROM enrollments WHERE username=? ORDER BY ts DESC LIMIT 1", (name,),
        ).fetchone()
        entries.append(UserListEntry(
            username=name,
            has_totp=ga.exists(),
            disabled=(name in denied),
            last_issued_at=last_row["ts"] if last_row else None,
        ))
    return entries
```

**Add `ensure_home` helper** above `enroll_user`:
```python
def ensure_home(home_dir: str, ldap_user: LdapUser) -> Path:
    """Create /home/<u>/ with chown to LDAP uid/gid + mode 0700 if missing."""
    home = safe_home_path(home_dir, ldap_user.username)
    if not home.exists():
        home.mkdir(parents=True)
        try:
            os.chown(home, ldap_user.uid_number, ldap_user.gid_number)
        except PermissionError:
            pass  # running non-root in tests
        os.chmod(home, 0o700)
    return home
```

**Replace the first three lines inside `enroll_user`** (the safe_home_path + UserNotFound check):
```python
def enroll_user(
    home_dir: str, conn: sqlite3.Connection, *,
    username: str, actor_type: str, actor_id: int, issuer: str,
) -> EnrollResult:
    if not is_valid_username(username):
        raise InvalidUsername(username)
    ldap_user = ldap_client.get_user(username)
    if ldap_user is None:
        raise UserNotFound(username)
    home_path = ensure_home(home_dir, ldap_user)

    had_secret = (home_path / ".google_authenticator").exists()
    # ... rest of the function unchanged (generate_enrollment, atomic write, chown by ldap_user, INSERT enrollments) ...
```

In the same function, **replace** the `try: stat = home_path.stat(); os.chown(ga, stat.st_uid, stat.st_gid)` block with the LDAP-known uid:
```python
    try:
        os.chown(ga, ldap_user.uid_number, ldap_user.gid_number)
    except PermissionError:
        pass
```

**Inside `revoke_user`** — after the existing `conn.execute(INSERT enrollments…)`, add:
```python
    ldap_client.invalidate_cache()
```

**Inside `enable_user`** — same:
```python
    ldap_client.invalidate_cache()
```

- [ ] **Step 4: Run all users tests**

```bash
docker compose build admin
docker compose run --rm admin pytest tests/test_users.py -v
```
Expected: ALL PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add admin/app/users.py admin/tests/test_users.py
git commit -m "feat(admin): switch users.list/enroll to LDAP-sourced identities + ensure_home"
```

---

## Task 5: Catch LdapUnavailable in API routes → 503

**Files:**
- Modify: `admin/app/routes_api.py`
- Modify: `admin/tests/test_routes_api.py`

- [ ] **Step 1: Add failing test**

Append to `admin/tests/test_routes_api.py`:
```python
def test_api_list_users_returns_503_when_ldap_down(env):
    from app import ldap_client as lc
    from app.ldap_client import LdapUnavailable
    import pytest
    pytest_monkey = pytest.MonkeyPatch()
    pytest_monkey.setattr(lc, "list_users", lambda: (_ for _ in ()).throw(LdapUnavailable("down")))
    try:
        client = TestClient(app)
        tok = _make_token(env["conn"], ["read"])
        r = client.get("/api/v1/users", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 503
        assert r.json()["error"] == "ldap_unavailable"
    finally:
        pytest_monkey.undo()
```

(Note: depends on the existing `env` fixture in `test_routes_api.py` populating LDAP mock — for this test, just bypass and stub.)

- [ ] **Step 2: Run, verify FAIL**

```bash
docker compose run --rm admin pytest tests/test_routes_api.py::test_api_list_users_returns_503_when_ldap_down -v
```
Expected: FAIL — currently raises 500.

- [ ] **Step 3: Add exception handler in routes_api.py**

Open `admin/app/routes_api.py`. Add import at top:
```python
from app.ldap_client import LdapUnavailable
```

Wrap each route that calls `list_users` / `get_user` in a try/except. Simplest: register a FastAPI exception handler at module load. Since `routes_api.py` uses a `router`, add the handler in `app/main.py` instead:

Open `admin/app/main.py` and add (after the existing NeedsLogin handler):
```python
from app.ldap_client import LdapUnavailable
from fastapi.responses import JSONResponse

@app.exception_handler(LdapUnavailable)
async def _ldap_unavailable(request, exc):
    # for HTML routes the dashboard handler swallows this and renders a banner;
    # for API routes we return a JSON 503.
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=503,
            content={"error": "ldap_unavailable", "detail": str(exc)},
        )
    # let HTML routes handle it themselves (see Task 6)
    raise exc
```

- [ ] **Step 4: Run, verify PASS**

```bash
docker compose run --rm admin pytest tests/test_routes_api.py -v
```
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add admin/app/main.py admin/tests/test_routes_api.py
git commit -m "feat(admin): API returns 503 ldap_unavailable when LDAP is down"
```

---

## Task 6: Web routes — skeleton, htmx partial, Refresh, error banner

**Files:**
- Modify: `admin/app/routes_web.py`
- Modify: `admin/app/templates/dashboard.html`
- Create: `admin/app/templates/_users_table.html`
- Modify: `admin/tests/test_routes_web.py`

- [ ] **Step 1: Write failing tests**

Append to `admin/tests/test_routes_web.py`:
```python
def test_users_list_partial_returns_only_tbody(env, fake_ldap):
    client = _client()
    _login(client, env)
    r = client.get("/users/_list")
    assert r.status_code == 200
    assert "<table" not in r.text   # tbody only
    assert "alice" in r.text


def test_refresh_invalidates_cache_and_redirects(env, fake_ldap):
    calls = []
    import app.ldap_client as lc
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(lc, "invalidate_cache", lambda: calls.append(1))
    try:
        client = _client()
        _login(client, env)
        # need csrf for /users/_refresh — fetch dashboard first
        import re
        page = client.get("/").text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        r = client.post("/users/_refresh", data={"csrf_token": csrf}, follow_redirects=False)
        assert r.status_code in (302, 303)
        assert "/" == r.headers["location"]
        assert calls == [1]
    finally:
        mp.undo()


def test_dashboard_renders_ldap_error_banner_when_down(env):
    from app import ldap_client as lc
    from app.ldap_client import LdapUnavailable
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(lc, "list_users", lambda: (_ for _ in ()).throw(LdapUnavailable("down")))
    mp.setattr(lc, "stale_users", lambda: [])
    try:
        client = _client()
        _login(client, env)
        r = client.get("/users/_list")
        assert r.status_code == 200
        assert "LDAP" in r.text and ("недоступен" in r.text or "unavailable" in r.text.lower())
    finally:
        mp.undo()
```

You need a top-level `fake_ldap` fixture in `test_routes_web.py` — add it next to the `env` fixture:
```python
@pytest.fixture
def fake_ldap(monkeypatch):
    from app.ldap_client import LdapUser
    users = [
        LdapUser(username="alice", uid_number=2001, gid_number=2001,
                 display_name="Alice", email="alice@vpn.local"),
    ]
    monkeypatch.setattr("app.ldap_client.list_users", lambda: users)
    monkeypatch.setattr("app.ldap_client.get_user",
                        lambda u: next((x for x in users if x.username == u), None))
    monkeypatch.setattr("app.ldap_client.invalidate_cache", lambda: None)
    monkeypatch.setattr("app.ldap_client.cache_age_seconds", lambda: 5)
    monkeypatch.setattr("app.ldap_client.stale_users", lambda: users)
```

- [ ] **Step 2: Run, verify FAIL**

```bash
docker compose run --rm admin pytest tests/test_routes_web.py -v -k "list_partial or refresh_invalidates or ldap_error_banner"
```
Expected: 404 / template errors.

- [ ] **Step 3: Create _users_table.html partial**

`admin/app/templates/_users_table.html`:
```html
{% if ldap_error %}
<tr><td colspan="5" style="color:darkorange">
  ⚠ LDAP недоступен ({{ ldap_error }}). Показан кэш.
</td></tr>
{% endif %}
{% for u in users %}
<tr>
  <td>{{ u.username }}</td>
  <td>{% if u.has_totp %}✓{% else %}—{% endif %}</td>
  <td>{% if u.disabled %}disabled{% elif u.has_totp %}active{% else %}no key{% endif %}</td>
  <td>{{ u.last_issued_at or "—" }}</td>
  <td>
    <form method="post" action="/users/{{ u.username }}/enroll" style="display:inline">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button {% if ldap_error %}disabled{% endif %}>
        {% if u.has_totp %}Re-issue{% else %}Issue{% endif %}
      </button>
    </form>
    {% if u.disabled %}
    <form method="post" action="/users/{{ u.username }}/enable" style="display:inline">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button {% if ldap_error %}disabled{% endif %}>Enable</button>
    </form>
    {% else %}
    <form method="post" action="/users/{{ u.username }}/revoke" style="display:inline"
          onsubmit="return confirm('Revoke {{ u.username }}?')">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button>Revoke</button>
    </form>
    {% endif %}
  </td>
</tr>
{% endfor %}
```

- [ ] **Step 4: Update dashboard.html**

Replace the body block in `admin/app/templates/dashboard.html` with:
```html
{% extends "base.html" %}
{% block body %}
<header>
  <strong>ocserv admin</strong> — {{ admin.username }}
  <nav>
    <a href="/">Users</a> · <a href="/tokens">API Tokens</a> · <a href="/audit">Audit</a>
    · <form method="post" action="/logout" style="display:inline">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit">Logout</button>
      </form>
  </nav>
</header>

<p>
  <form method="post" action="/users/_refresh" style="display:inline">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button type="submit">🔄 Refresh</button>
  </form>
  {% if cache_age is not none %}
  <small>список обновлён {{ cache_age }} сек назад · из LDAP</small>
  {% endif %}
</p>

<table>
  <thead><tr><th>User</th><th>TOTP</th><th>Status</th><th>Last issued</th><th>Actions</th></tr></thead>
  <tbody hx-get="/users/_list" hx-trigger="load" hx-swap="innerHTML">
    <tr><td colspan="5">⏳ Загружаем юзеров из LDAP...</td></tr>
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Refactor routes_web.py dashboard + add new routes**

Open `admin/app/routes_web.py`. **Replace the existing `dashboard` route** with:

```python
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: AdminRow = Depends(require_admin_web),
    conn=Depends(get_conn),
):
    from app import ldap_client
    csrf = generate_csrf_token()
    response = templates.TemplateResponse(
        request, "dashboard.html",
        {"admin": admin, "csrf_token": csrf,
         "cache_age": ldap_client.cache_age_seconds()},
    )
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=True,
                        samesite="strict", path="/")
    return response
```

(Note: the old `body.replace('value=""', …)` hack is removed — `csrf_token` is passed directly into the template context.)

**Add new routes** to `routes_web.py`:

```python
@router.get("/users/_list", response_class=HTMLResponse)
def users_list_partial(
    request: Request,
    admin: AdminRow = Depends(require_admin_web),
    conn=Depends(get_conn),
):
    from app import ldap_client
    from app.ldap_client import LdapUnavailable
    settings = get_settings()
    csrf = request.cookies.get(CSRF_COOKIE, "")
    ldap_error = None
    try:
        users = list_users(settings.home_dir, settings.disabled_users_path, conn)
    except LdapUnavailable as e:
        ldap_error = str(e)
        # fall back to stale data if present
        stale = ldap_client.stale_users() or []
        # decorate with file/denylist state same as list_users does
        from pathlib import Path as _P
        from app.users import _read_denylist
        denied = set(_read_denylist(settings.disabled_users_path))
        users = []
        for su in stale:
            ga = _P(settings.home_dir) / su.username / ".google_authenticator"
            users.append({"username": su.username, "has_totp": ga.exists(),
                          "disabled": su.username in denied, "last_issued_at": None})
    return templates.TemplateResponse(
        request, "_users_table.html",
        {"users": users, "csrf_token": csrf, "ldap_error": ldap_error},
    )


@router.post("/users/_refresh")
def users_refresh(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    admin: AdminRow = Depends(require_admin_web),
):
    _require_csrf(csrf_token, csrf_cookie)
    from app import ldap_client
    ldap_client.invalidate_cache()
    return RedirectResponse("/", status_code=303)
```

- [ ] **Step 6: Run all web-route tests**

```bash
docker compose build admin
docker compose run --rm admin pytest tests/test_routes_web.py -v
```
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add admin/app/routes_web.py admin/app/templates/dashboard.html admin/app/templates/_users_table.html admin/tests/test_routes_web.py
git commit -m "feat(admin): htmx skeleton dashboard with LDAP cache indicator + Refresh + stale fallback"
```

---

## Task 7: Dedicated LDAP-failure test (revoke still works)

**Files:**
- Create: `admin/tests/test_ldap_failure.py`

- [ ] **Step 1: Write the test file**

`admin/tests/test_ldap_failure.py`:
```python
"""When LDAP is down, the admin panel must still allow revoke + show stale list."""
import pytest
from pathlib import Path
from app import ldap_client
from app.ldap_client import LdapUnavailable, LdapUser
from app.users import revoke_user


@pytest.fixture
def env(tmp_path):
    from app.db import init_db, connect
    home = tmp_path / "home"; home.mkdir()
    (home / "alice").mkdir()
    (home / "alice" / ".google_authenticator").write_text("S\n", encoding="utf-8")
    control = tmp_path / "control"; control.mkdir()
    (control / "disabled-users").write_text("", encoding="utf-8")
    db = str(tmp_path / "a.db")
    init_db(db)
    return {"conn": connect(db), "home": str(home),
            "denylist": str(control / "disabled-users")}


def test_revoke_works_with_ldap_down(env, monkeypatch):
    monkeypatch.setattr(ldap_client, "list_users",
                        lambda: (_ for _ in ()).throw(LdapUnavailable("down")))
    # revoke does NOT call ldap; should not raise
    revoke_user(env["home"], env["denylist"], env["conn"],
                username="alice", actor_type="admin", actor_id=1)
    assert "alice" in Path(env["denylist"]).read_text()
    assert not (Path(env["home"]) / "alice" / ".google_authenticator").exists()


def test_stale_users_returned_after_first_success(monkeypatch):
    ldap_client.invalidate_cache()
    users = [LdapUser(username="alice", uid_number=2001, gid_number=2001,
                      display_name=None, email=None)]
    # first call: success → caches + sets _stale_data
    monkeypatch.setattr(ldap_client, "_connect", lambda: (_ for _ in ()).throw(StopIteration))
    # bypass by directly populating cache
    import time
    ldap_client._cache["data"] = users
    ldap_client._cache["ts"] = time.time()
    ldap_client._stale_data = users
    # invalidate forces refetch; refetch fails
    ldap_client.invalidate_cache()
    with pytest.raises(LdapUnavailable):
        ldap_client.list_users()
    # stale data still available
    assert ldap_client.stale_users() == users
```

- [ ] **Step 2: Run**

```bash
docker compose run --rm admin pytest tests/test_ldap_failure.py -v
```
Expected: 2 PASS.

- [ ] **Step 3: Commit**

```bash
git add admin/tests/test_ldap_failure.py
git commit -m "test(admin): verify revoke works and stale_users survives LDAP outage"
```

---

## Task 8: E2E smoke (replaces the existing script)

**Files:**
- Modify: `tests/e2e/test_admin_e2e.sh`

- [ ] **Step 1: Rewrite the E2E script**

Replace `tests/e2e/test_admin_e2e.sh` body with:
```bash
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
```

- [ ] **Step 2: Make executable and run**

```bash
chmod +x tests/e2e/test_admin_e2e.sh
./tests/e2e/test_admin_e2e.sh
```
Expected: ends with `[e2e] OK`.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_admin_e2e.sh
git commit -m "test(e2e): rewrite smoke test for LDAP-backed user source"
```

---

## Task 9: README — LDAP section + updated quick-start

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add new sections to README**

Insert the following section **after** the existing "Что внутри" section and **before** "Быстрый старт":

```markdown
## Источник юзеров: LDAP

Юзеры **не** живут в `/etc/passwd` контейнера и **не** заводятся через `users.env` (этот файл удалён). Источник истины — OpenLDAP-контейнер, поднимаемый тем же `docker compose`.

Layout:
```
dc=vpn,dc=local
├── ou=users (alice, bob)
└── ou=groups
    └── cn=vpn-users (только эти юзеры могут в VPN)
```

Чтобы добавить нового юзера — см. `ldap/README.md`.
```

Replace the "Быстрый старт" step about TOTP enrollment with:
```markdown
### 2. Выпустить TOTP

Открой админку https://localhost:8443/, залогинься (`admin1` / см. `.env`), на дашборде нажми **Issue** напротив alice. Получишь QR-код один раз.

(Старый CLI `docker exec -it ocserv totp-enroll alice` больше не работает — юзеры LDAP-ные, home-папка создаётся самой админкой при Issue.)
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README — LDAP user source + updated quick start"
```

---

## Self-Review Notes

Cross-checked plan against spec sections:
- §1 Decisions table — all 9 implemented (Task 1 LDAP service, Task 2 PAM + remove users.env, Task 3 ldap_client + cache, Task 4 users.py + ensure_home, Task 5 503, Task 6 UX/skeleton/Refresh, Task 7 LDAP-down resilience).
- §2 Architecture — Task 1 compose service + Task 2 ocserv pam_ldap + Task 4 admin reads via ldap_client. No docker.sock, no privileged.
- §3 Schema/seed — Task 1 covers all 4 LDIFs including readonly account.
- §4 ldap_client + users.py — Task 3 + Task 4.
- §5 PAM stack + ldap.conf — Task 2.
- §6 UX (skeleton, indicator, Refresh, stale fallback) — Task 6 + Task 7.
- §7 Tests — Task 3 (ldap_client), Task 4 (users), Task 5 (API 503), Task 6 (web partial + banner), Task 7 (failure), Task 8 (E2E).
- §8 Files — covered by Tasks 1-9.
- §9 Out-of-scope — not implemented (correct).
- §10 Risks — fail-closed (Task 2 ldap.conf timeouts), stale fallback (Task 6+7), chmod 600 ldap.conf (Task 2), ensure_home only-if-missing (Task 4).

Type/name consistency: `LdapUser`, `LdapUnavailable`, `ensure_home`, `_users_table.html`, `/users/_list`, `/users/_refresh` — all referenced across tasks match their definitions.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-24-ldap-integration.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
