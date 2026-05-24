import pytest
import pyotp
from passlib.hash import bcrypt
from app.db import init_db, connect
from app.auth import (
    bootstrap_admin_if_needed, verify_password, set_admin_totp,
    verify_admin_totp, AuthFailed, TOTPRequired,
)


@pytest.fixture
def conn(tmp_path):
    db = str(tmp_path / "a.db")
    init_db(db)
    return connect(db)


def test_bootstrap_creates_admin_once(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1


def test_verify_password_ok(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    admin = verify_password(conn, username="admin1", password="pw")
    assert admin.username == "admin1"


def test_verify_password_fail(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    with pytest.raises(AuthFailed):
        verify_password(conn, username="admin1", password="WRONG")


def test_set_totp_and_verify(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    secret = "JBSWY3DPEHPK3PXP"
    set_admin_totp(conn, admin_id=1, secret=secret)
    code = pyotp.TOTP(secret).now()
    verify_admin_totp(conn, admin_id=1, code=code)


def test_verify_totp_wrong_code(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    set_admin_totp(conn, admin_id=1, secret="JBSWY3DPEHPK3PXP")
    with pytest.raises(AuthFailed):
        verify_admin_totp(conn, admin_id=1, code="000000")


def test_verify_password_when_no_totp_yet_signals_required(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    admin = verify_password(conn, username="admin1", password="pw")
    with pytest.raises(TOTPRequired):
        verify_admin_totp(conn, admin_id=admin.id, code="123456")


def test_verify_totp_rejects_replay_same_code(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    secret = "JBSWY3DPEHPK3PXP"
    set_admin_totp(conn, admin_id=1, secret=secret)
    code = pyotp.TOTP(secret).now()
    verify_admin_totp(conn, admin_id=1, code=code)          # first use: ok
    with pytest.raises(AuthFailed):                          # replay: rejected
        verify_admin_totp(conn, admin_id=1, code=code)


def test_verify_totp_rejects_malformed_code(conn):
    h = bcrypt.using(rounds=4).hash("pw")
    bootstrap_admin_if_needed(conn, username="admin1", password_hash=h)
    set_admin_totp(conn, admin_id=1, secret="JBSWY3DPEHPK3PXP")
    for bad in ("", "abcdef", "12345", "1234567", "12 456"):
        with pytest.raises(AuthFailed):
            verify_admin_totp(conn, admin_id=1, code=bad)
