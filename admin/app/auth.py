import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import pyotp
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthFailed(Exception):
    pass


class TOTPRequired(Exception):
    """Raised when admin has no TOTP enrolled — must enroll first."""


@dataclass(frozen=True)
class AdminRow:
    id: int
    username: str
    totp_enrolled: bool


def bootstrap_admin_if_needed(conn: sqlite3.Connection, *, username: str, password_hash: str) -> None:
    row = conn.execute("SELECT id FROM admins WHERE username=?", (username,)).fetchone()
    if row:
        return
    conn.execute(
        "INSERT INTO admins(username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def verify_password(conn: sqlite3.Connection, *, username: str, password: str) -> AdminRow:
    row = conn.execute(
        "SELECT id, username, password_hash, totp_secret FROM admins WHERE username=?", (username,),
    ).fetchone()
    if row is None:
        # constant-time-ish: still hash a dummy
        _pwd.dummy_verify()
        raise AuthFailed()
    if not _pwd.verify(password, row["password_hash"]):
        raise AuthFailed()
    return AdminRow(id=row["id"], username=row["username"], totp_enrolled=row["totp_secret"] is not None)


def set_admin_totp(conn: sqlite3.Connection, *, admin_id: int, secret: str) -> None:
    conn.execute(
        "UPDATE admins SET totp_secret=?, totp_enrolled_at=? WHERE id=?",
        (secret, datetime.now(timezone.utc).isoformat(timespec="seconds"), admin_id),
    )


def verify_admin_totp(conn: sqlite3.Connection, *, admin_id: int, code: str) -> None:
    row = conn.execute("SELECT totp_secret FROM admins WHERE id=?", (admin_id,)).fetchone()
    if row is None:
        raise AuthFailed()
    if row["totp_secret"] is None:
        raise TOTPRequired()
    if not pyotp.TOTP(row["totp_secret"]).verify(code, valid_window=1):
        raise AuthFailed()
