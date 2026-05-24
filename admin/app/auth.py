import hmac
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import pyotp
from passlib.context import CryptContext

TOTP_WINDOW = 1  # ± steps accepted around current step (each step = 30s)

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
    row = conn.execute(
        "SELECT totp_secret, last_used_totp_step FROM admins WHERE id=?", (admin_id,),
    ).fetchone()
    if row is None:
        raise AuthFailed()
    if row["totp_secret"] is None:
        raise TOTPRequired()
    if not code or not code.isdigit() or len(code) != 6:
        raise AuthFailed()

    totp = pyotp.TOTP(row["totp_secret"])
    now = int(time.time())
    step_size = totp.interval
    current_step = now // step_size
    last_used = row["last_used_totp_step"]

    # Iterate ± TOTP_WINDOW steps, find which one the code corresponds to.
    matched_step: int | None = None
    for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        step = current_step + offset
        expected = totp.at(step * step_size)
        if hmac.compare_digest(expected, code):
            matched_step = step
            break
    if matched_step is None:
        raise AuthFailed()

    # Replay protection: reject codes from a step <= the last successfully used step.
    if last_used is not None and matched_step <= last_used:
        raise AuthFailed()

    conn.execute(
        "UPDATE admins SET last_used_totp_step=? WHERE id=?", (matched_step, admin_id),
    )
