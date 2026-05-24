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
