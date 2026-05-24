"""Unit tests for ldap_client using ldap3 MOCK_SYNC.

MOCK_SYNC creates an in-memory LDAP server; no real slapd needed.
"""
import pytest
from ldap3 import Connection, Server, MOCK_SYNC, OFFLINE_SLAPD_2_4
from app import ldap_client
from app.ldap_client import LdapUnavailable


def _seed(conn):
    conn.strategy.add_entry(
        "cn=admin-readonly,dc=vpn,dc=local",
        {"objectClass": ["simpleSecurityObject", "organizationalRole"],
         "cn": "admin-readonly", "userPassword": "bindpw"},
    )
    for ou in ("users", "groups"):
        conn.strategy.add_entry(
            f"ou={ou},dc=vpn,dc=local",
            {"objectClass": ["organizationalUnit"], "ou": ou},
        )
    for uid, uidnum in [("alice", 2001), ("bob", 2002)]:
        conn.strategy.add_entry(
            f"uid={uid},ou=users,dc=vpn,dc=local",
            {"objectClass": ["inetOrgPerson", "posixAccount"],
             "uid": uid, "cn": uid.capitalize(), "sn": uid.capitalize(),
             "uidNumber": uidnum, "gidNumber": uidnum,
             "homeDirectory": f"/home/{uid}", "loginShell": "/bin/bash",
             "mail": f"{uid}@vpn.local", "userPassword": "{SSHA}xxx"},
        )
    conn.strategy.add_entry(
        "uid=carol,ou=users,dc=vpn,dc=local",
        {"objectClass": ["inetOrgPerson", "posixAccount"],
         "uid": "carol", "cn": "Carol", "sn": "Carol",
         "uidNumber": 2003, "gidNumber": 2003,
         "homeDirectory": "/home/carol", "loginShell": "/bin/bash",
         "mail": "carol@vpn.local", "userPassword": "{SSHA}xxx"},
    )
    conn.strategy.add_entry(
        "cn=vpn-users,ou=groups,dc=vpn,dc=local",
        {"objectClass": ["posixGroup"], "cn": "vpn-users",
         "gidNumber": 3000, "memberUid": ["alice", "bob"]},
    )


@pytest.fixture
def mock_ldap(monkeypatch):
    """Patch ldap_client._connect to return a freshly-seeded MOCK_SYNC connection."""
    server = Server("mock", get_info=OFFLINE_SLAPD_2_4)

    def _factory(*args, **kwargs):
        c = Connection(server, user="cn=admin-readonly,dc=vpn,dc=local",
                       password="bindpw", client_strategy=MOCK_SYNC)
        _seed(c)
        c.bind()
        return c

    monkeypatch.setattr(ldap_client, "_connect", _factory)
    monkeypatch.setenv("ADMIN_LDAP_BIND_DN", "cn=admin-readonly,dc=vpn,dc=local")
    monkeypatch.setenv("ADMIN_LDAP_BIND_PASSWORD", "bindpw")
    ldap_client.invalidate_cache()
    yield


def test_list_users_returns_only_vpn_group_members(mock_ldap):
    users = ldap_client.list_users()
    names = sorted(u.username for u in users)
    assert names == ["alice", "bob"]


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
    assert ldap_client.get_user("carol") is None


def test_cache_hit_skips_ldap(mock_ldap, monkeypatch):
    ldap_client.list_users()
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
