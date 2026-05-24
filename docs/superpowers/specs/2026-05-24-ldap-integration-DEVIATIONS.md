# LDAP Integration — Execution Deviations

## Task 1 — osixia/openldap bootstrap mount path

**Plan:** mount `./ldap/bootstrap` into `/container/service/slapd/assets/config/bootstrap/ldif/custom:ro`.

**Reality:** osixia's startup script (1) chowns the mount and rejects `:ro`, and (2) on first start runs `rm -rf` on the parent `assets/config` dir, which wipes the host-side LDIFs through the bind mount.

**Workaround:** mount at a neutral path `/opt/ldap-seed` and set `LDAP_SEED_INTERNAL_LDIF_PATH=/opt/ldap-seed`. The startup script then `cp -R`s into the assets dir instead of mutating our mount.

## Task 1 — readonly account via env, not LDIF

**Plan:** `ldap/bootstrap/04-readonly.ldif` adds `cn=admin-readonly,dc=vpn,dc=local` with a pre-hashed `userPassword`.

**Reality:** the seed LDIF doesn't grant the entry read access to `ou=users` (default ACL only allows `cn=admin` and the entry's own DN). The admin-readonly bind worked but every search returned `err=32 No such object`.

**Workaround:** use osixia's first-class `LDAP_READONLY_USER=true` + `LDAP_READONLY_USER_USERNAME=admin-readonly` + `LDAP_READONLY_USER_PASSWORD=$ADMIN_LDAP_BIND_PASSWORD`. The image then provisions both the entry and a `to * by dn.exact="cn=admin-readonly,dc=vpn,dc=local" read` ACL. `04-readonly.ldif` was removed.

## Task 1 — healthcheck binds as admin

**Plan:** anonymous-bind ldapsearch as healthcheck.

**Reality:** default ACL forbids anonymous read of `dc=vpn,dc=local`, so anonymous search returned `err=32` even on a fully-seeded server, leaving the container forever `unhealthy`.

**Workaround:** healthcheck now binds as `cn=admin,dc=vpn,dc=local` with `$LDAP_ADMIN_PASSWORD` (escaped `$$` in compose so the shell, not Compose, expands the env var at exec time).
