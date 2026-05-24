"""When LDAP is down, the admin panel must still allow revoke + show stale list."""
import time
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
    monkeypatch.setattr(ldap_client, "invalidate_cache", lambda: None)
    # revoke does NOT call ldap; should not raise
    revoke_user(env["home"], env["denylist"], env["conn"],
                username="alice", actor_type="admin", actor_id=1)
    assert "alice" in Path(env["denylist"]).read_text()
    assert not (Path(env["home"]) / "alice" / ".google_authenticator").exists()


def test_stale_users_returned_after_first_success(monkeypatch):
    ldap_client.invalidate_cache()
    users = [LdapUser(username="alice", uid_number=2001, gid_number=2001,
                      display_name=None, email=None)]
    # Pretend a previous successful fetch left _stale_data populated.
    ldap_client._cache["data"] = users
    ldap_client._cache["ts"] = time.time()
    ldap_client._stale_data = users
    # Now invalidate the cache and break _connect → next fetch must raise.
    ldap_client.invalidate_cache()
    monkeypatch.setattr(ldap_client, "_connect",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(LdapUnavailable):
        ldap_client.list_users()
    # Stale data still available for UX fallback
    assert ldap_client.stale_users() == users
