import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, connect
from app.config import get_settings
from app.deps import get_conn
from app.tokens import create_token


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "alice").mkdir()
    (home / "bob").mkdir()
    (home / "alice" / ".google_authenticator").write_text("S\n", encoding="utf-8")
    control = tmp_path / "control"
    control.mkdir()
    (control / "disabled-users").write_text("", encoding="utf-8")
    db = str(tmp_path / "a.db")
    init_db(db)
    conn = connect(db)
    conn.execute("INSERT INTO admins(id, username, password_hash, created_at) VALUES (1, 'a', 'h', datetime('now'))")

    settings = get_settings.__wrapped__() if hasattr(get_settings, "__wrapped__") else None
    monkeypatch.setenv("ADMIN_HOME_DIR", str(home))
    monkeypatch.setenv("ADMIN_DISABLED_USERS_PATH", str(control / "disabled-users"))
    monkeypatch.setenv("ADMIN_DB_PATH", db)

    app.dependency_overrides[get_conn] = lambda: conn
    yield {"conn": conn, "home": str(home), "denylist": str(control / "disabled-users")}
    app.dependency_overrides.clear()


def _make_token(conn, scopes):
    return create_token(conn, name="t", scopes=scopes, created_by_admin_id=1).plaintext


def test_list_users_requires_token():
    client = TestClient(app)
    r = client.get("/api/v1/users")
    assert r.status_code == 401


def test_list_users_with_read_scope(env):
    client = TestClient(app)
    tok = _make_token(env["conn"], ["read"])
    r = client.get("/api/v1/users", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    names = sorted(u["username"] for u in r.json())
    assert names == ["alice", "bob"]


def test_enroll_requires_enroll_scope(env):
    client = TestClient(app)
    tok_readonly = _make_token(env["conn"], ["read"])
    r = client.post("/api/v1/users/bob/enroll", headers={"Authorization": f"Bearer {tok_readonly}"})
    assert r.status_code == 403


def test_enroll_returns_secret_once(env):
    client = TestClient(app)
    tok = _make_token(env["conn"], ["enroll"])
    r = client.post("/api/v1/users/bob/enroll", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert "secret" in body
    assert "qr_png_base64" in body
    assert len(body["scratch_codes"]) == 5
    # subsequent GET shouldn't expose secret
    r2 = client.get(f"/api/v1/users/bob", headers={"Authorization": f"Bearer {tok}"})
    assert "secret" not in r2.json()


def test_enroll_invalid_username(env):
    client = TestClient(app)
    tok = _make_token(env["conn"], ["enroll"])
    r = client.post("/api/v1/users/..%2Fetc/enroll", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (400, 404)


def test_revoke_then_enable(env):
    from pathlib import Path
    client = TestClient(app)
    tok = _make_token(env["conn"], ["revoke"])
    client.post("/api/v1/users/alice/revoke", headers={"Authorization": f"Bearer {tok}"})
    assert "alice" in Path(env["denylist"]).read_text()
    client.post("/api/v1/users/alice/enable", headers={"Authorization": f"Bearer {tok}"})
    assert "alice" not in Path(env["denylist"]).read_text()
