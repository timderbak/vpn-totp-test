import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from app.main import app
from app.db import init_db, connect
from app.deps import get_conn
from app.auth import bootstrap_admin_if_needed, set_admin_totp
from app.ldap_client import LdapUser


@pytest.fixture(autouse=True)
def fake_ldap(monkeypatch):
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "admin1")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", bcrypt.using(rounds=4).hash("pw"))
    monkeypatch.setenv("ADMIN_COOKIE_SECRET", "0" * 64)

    db = str(tmp_path / "a.db")
    init_db(db)
    conn = connect(db)
    bootstrap_admin_if_needed(conn, username="admin1",
                              password_hash=bcrypt.using(rounds=4).hash("pw"))
    home = tmp_path / "home"; home.mkdir(); (home / "alice").mkdir()
    control = tmp_path / "control"; control.mkdir()
    (control / "disabled-users").write_text("", encoding="utf-8")
    monkeypatch.setenv("ADMIN_HOME_DIR", str(home))
    monkeypatch.setenv("ADMIN_DISABLED_USERS_PATH", str(control / "disabled-users"))
    monkeypatch.setenv("ADMIN_DB_PATH", db)

    app.dependency_overrides[get_conn] = lambda: conn
    yield {"conn": conn, "home": str(home), "denylist": str(control / "disabled-users")}
    app.dependency_overrides.clear()


def _client():
    # https base_url is required so the TestClient retains Secure cookies.
    return TestClient(app, base_url="https://testserver")


def test_login_page_renders(env):
    client = _client()
    r = client.get("/login")
    assert r.status_code == 200
    assert "<form" in r.text


def test_login_wrong_password_shows_error(env):
    client = _client()
    r = client.post("/login", data={"username": "admin1", "password": "WRONG"}, follow_redirects=False)
    assert r.status_code in (200, 401)
    assert "invalid" in r.text.lower() or "wrong" in r.text.lower()


def test_login_correct_password_advances_to_totp_step(env):
    client = _client()
    r = client.post("/login", data={"username": "admin1", "password": "pw"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/login/totp" in r.headers["location"]


def test_totp_step_rate_limited_after_5_fails(env):
    """After 5 failed TOTP attempts from same IP, /login/totp returns 429."""
    set_admin_totp(env["conn"], admin_id=1, secret="JBSWY3DPEHPK3PXP")
    client = _client()
    client.post("/login", data={"username": "admin1", "password": "pw"})
    # 5 failed attempts → still 401
    for _ in range(5):
        r = client.post("/login/totp", data={"code": "000000"}, follow_redirects=False)
        assert r.status_code == 401
        # re-acquire pending cookie (route deletes it on fail)
        client.post("/login", data={"username": "admin1", "password": "pw"})
    # 6th attempt → 429 (limiter triggers BEFORE verify, so a fresh pending doesn't help)
    r = client.post("/login/totp", data={"code": "000000"}, follow_redirects=False)
    assert r.status_code == 429
    assert r.headers.get("retry-after")


def test_first_login_forces_admin_totp_enroll(env):
    client = _client()
    client.post("/login", data={"username": "admin1", "password": "pw"}, follow_redirects=False)
    r = client.get("/login/totp", follow_redirects=False)
    # admin has no totp_secret yet — should redirect to enrollment page
    assert r.status_code in (302, 303)
    assert "/login/enroll-totp" in r.headers["location"]


def test_completed_login_sets_session_cookie(env):
    import pyotp
    secret = "JBSWY3DPEHPK3PXP"
    set_admin_totp(env["conn"], admin_id=1, secret=secret)
    client = _client()
    client.post("/login", data={"username": "admin1", "password": "pw"}, follow_redirects=False)
    code = pyotp.TOTP(secret).now()
    r = client.post("/login/totp", data={"code": code}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "__Host-admin_session" in r.cookies or any("__Host-admin_session" in c for c in r.headers.get("set-cookie", "").split(","))


import pyotp
from passlib.hash import bcrypt
from app.auth import set_admin_totp as _set_admin_totp


def _login(client, env):
    _set_admin_totp(env["conn"], admin_id=1, secret="JBSWY3DPEHPK3PXP")
    client.post("/login", data={"username": "admin1", "password": "pw"})
    code = pyotp.TOTP("JBSWY3DPEHPK3PXP").now()
    client.post("/login/totp", data={"code": code})


def test_dashboard_requires_session(env):
    client = _client()
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


def test_dashboard_lists_users(env):
    # Dashboard is now a skeleton; the user list arrives via htmx GET /users/_list.
    client = _client()
    _login(client, env)
    r = client.get("/")
    assert r.status_code == 200
    assert 'hx-get="/users/_list"' in r.text
    # And the partial returns the user
    r2 = client.get("/users/_list")
    assert "alice" in r2.text


def test_enroll_via_form_requires_csrf(env):
    client = _client()
    _login(client, env)
    # POST without csrf token
    r = client.post("/users/alice/enroll", data={})
    assert r.status_code == 403


def test_enroll_via_form_shows_secret_once(env):
    import re
    client = _client()
    _login(client, env)
    page = client.get("/").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = client.post("/users/alice/enroll", data={"csrf_token": csrf})
    assert r.status_code == 200
    assert "Secret" in r.text or "secret" in r.text
    # subsequent dashboard render must not contain the secret
    page2 = client.get("/").text
    assert "Secret" not in page2 or "secret" not in page2.split("Secret")[0]


def test_create_token_form_shows_plaintext_once(env):
    import re
    client = _client()
    _login(client, env)
    page = client.get("/tokens").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = client.post("/tokens", data={"csrf_token": csrf, "name": "ci-bot",
                                     "scopes": "read,enroll"})
    assert r.status_code == 200
    assert "vpa_" in r.text


def test_audit_page_renders(env):
    client = _client()
    _login(client, env)
    r = client.get("/audit")
    assert r.status_code == 200
    assert "<table" in r.text


def test_users_list_partial_returns_only_tbody(env):
    client = _client()
    _login(client, env)
    r = client.get("/users/_list")
    assert r.status_code == 200
    assert "<table" not in r.text
    assert "alice" in r.text


def test_refresh_invalidates_cache_and_redirects(env, monkeypatch):
    calls = []
    monkeypatch.setattr("app.ldap_client.invalidate_cache", lambda: calls.append(1))
    client = _client()
    _login(client, env)
    import re
    page = client.get("/").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = client.post("/users/_refresh", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/"
    assert calls == [1]


def test_dashboard_renders_ldap_error_banner_when_down(env, monkeypatch):
    from app.ldap_client import LdapUnavailable
    def _raise():
        raise LdapUnavailable("down")
    monkeypatch.setattr("app.ldap_client.list_users", _raise)
    monkeypatch.setattr("app.ldap_client.stale_users", lambda: [])
    client = _client()
    _login(client, env)
    r = client.get("/users/_list")
    assert r.status_code == 200
    assert "LDAP" in r.text and ("недоступен" in r.text or "unavailable" in r.text.lower())
