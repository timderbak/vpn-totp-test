import fcntl
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.totp import (
    Enrollment, build_qr_png_base64, format_google_authenticator_file,
    generate_enrollment,
)
from app.usernames import InvalidUsername, is_valid_username, safe_home_path


class UserNotFound(LookupError):
    pass


@dataclass(frozen=True)
class UserListEntry:
    username: str
    has_totp: bool
    disabled: bool
    last_issued_at: str | None


@dataclass(frozen=True)
class EnrollResult:
    enrollment: Enrollment
    qr_png_base64: str


def _read_denylist(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    lines = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def _write_denylist_atomic(path: str, names: list[str]) -> None:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    body = "".join(f"{n}\n" for n in names)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _with_denylist_lock(path: str, fn):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("", encoding="utf-8")
    with open(p, "r+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def list_users(home_dir: str, denylist_path: str, conn: sqlite3.Connection) -> list[UserListEntry]:
    home = Path(home_dir)
    denied = set(_read_denylist(denylist_path))
    entries: list[UserListEntry] = []
    for child in sorted(home.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if not is_valid_username(name):
            continue
        ga = child / ".google_authenticator"
        last_row = conn.execute(
            "SELECT ts FROM enrollments WHERE username=? ORDER BY ts DESC LIMIT 1", (name,),
        ).fetchone()
        entries.append(UserListEntry(
            username=name,
            has_totp=ga.exists(),
            disabled=(name in denied),
            last_issued_at=last_row["ts"] if last_row else None,
        ))
    return entries


def enroll_user(
    home_dir: str, conn: sqlite3.Connection, *,
    username: str, actor_type: str, actor_id: int, issuer: str,
) -> EnrollResult:
    home_path = safe_home_path(home_dir, username)  # raises InvalidUsername
    if not home_path.exists():
        raise UserNotFound(username)

    had_secret = (home_path / ".google_authenticator").exists()
    enrollment = generate_enrollment(username=username, issuer=issuer)

    # write atomically into user's home
    ga = home_path / ".google_authenticator"
    tmp = ga.with_suffix(".tmp")
    tmp.write_text(format_google_authenticator_file(enrollment), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ga)
    try:
        # match real google-authenticator semantics: owned by user
        stat = home_path.stat()
        os.chown(ga, stat.st_uid, stat.st_gid)
    except PermissionError:
        # running as non-root in tests: skip
        pass

    fingerprint = hashlib.sha256(enrollment.secret.encode()).hexdigest()[:16]
    conn.execute(
        "INSERT INTO enrollments(username, action, actor_type, actor_id, totp_fingerprint, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, "re-issued" if had_secret else "issued",
         actor_type, actor_id, fingerprint,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    return EnrollResult(enrollment=enrollment, qr_png_base64=build_qr_png_base64(enrollment))


def revoke_user(
    home_dir: str, denylist_path: str, conn: sqlite3.Connection, *,
    username: str, actor_type: str, actor_id: int,
) -> None:
    home_path = safe_home_path(home_dir, username)
    if not home_path.exists():
        raise UserNotFound(username)

    def _do():
        names = _read_denylist(denylist_path)
        if username not in names:
            names.append(username)
            _write_denylist_atomic(denylist_path, sorted(set(names)))
        ga = home_path / ".google_authenticator"
        if ga.exists():
            ga.unlink()
    _with_denylist_lock(denylist_path, _do)

    conn.execute(
        "INSERT INTO enrollments(username, action, actor_type, actor_id, ts) VALUES (?, ?, ?, ?, ?)",
        (username, "revoked", actor_type, actor_id,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def enable_user(
    home_dir: str, denylist_path: str, conn: sqlite3.Connection, *,
    username: str, actor_type: str, actor_id: int,
) -> None:
    if not is_valid_username(username):
        raise InvalidUsername(username)

    def _do():
        names = _read_denylist(denylist_path)
        if username in names:
            names = [n for n in names if n != username]
            _write_denylist_atomic(denylist_path, names)
    _with_denylist_lock(denylist_path, _do)

    conn.execute(
        "INSERT INTO enrollments(username, action, actor_type, actor_id, ts) VALUES (?, ?, ?, ?, ?)",
        (username, "enabled", actor_type, actor_id,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
