import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from app.main import app
from app.db import init_db, connect
from app.deps import get_conn
from app.auth import bootstrap_admin_if_needed, set_admin_totp


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
