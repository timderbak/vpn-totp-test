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
    data = _fetch_all()
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
