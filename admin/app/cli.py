"""Simple CLI for managing admin accounts.

Usage (run inside the admin container):
    docker compose exec admin python -m app.cli add-admin alice
    docker compose exec admin python -m app.cli list-admins
    docker compose exec admin python -m app.cli remove-admin alice
    docker compose exec admin python -m app.cli reset-totp alice

`add-admin` prompts for a password (no echo), bcrypt-hashes it, and inserts
into the admins table. The new admin will be asked to enroll TOTP on first
login (existing flow in routes_web.py).
"""
import getpass
import sys
from datetime import datetime, timezone

from passlib.hash import bcrypt

from app.config import get_settings
from app.db import init_db, connect


def _conn():
    s = get_settings()
    init_db(s.db_path)
    return connect(s.db_path)


def cmd_add(username: str) -> int:
    conn = _conn()
    if conn.execute("SELECT 1 FROM admins WHERE username=?", (username,)).fetchone():
        print(f"admin '{username}' already exists", file=sys.stderr)
        return 1
    pw = getpass.getpass(f"new password for {username}: ")
    pw2 = getpass.getpass("repeat: ")
    if pw != pw2:
        print("passwords do not match", file=sys.stderr)
        return 1
    if len(pw) < 8:
        print("password must be >= 8 chars", file=sys.stderr)
        return 1
    hashed = bcrypt.using(rounds=12).hash(pw)
    conn.execute(
        "INSERT INTO admins(username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, hashed, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    print(f"admin '{username}' created — they will enroll TOTP on first login.")
    return 0


def cmd_list() -> int:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, username, "
        "  CASE WHEN totp_secret IS NULL THEN 'no' ELSE 'yes' END AS totp, "
        "  created_at FROM admins ORDER BY id"
    ).fetchall()
    print(f"{'ID':<4} {'USERNAME':<20} {'TOTP':<6} CREATED")
    for r in rows:
        print(f"{r['id']:<4} {r['username']:<20} {r['totp']:<6} {r['created_at']}")
    return 0


def cmd_remove(username: str) -> int:
    conn = _conn()
    n = conn.execute("DELETE FROM admins WHERE username=?", (username,)).rowcount
    if n == 0:
        print(f"no admin '{username}'", file=sys.stderr)
        return 1
    # cascade: kill their sessions too
    conn.execute("DELETE FROM sessions WHERE admin_id NOT IN (SELECT id FROM admins)")
    print(f"admin '{username}' removed.")
    return 0


def cmd_reset_totp(username: str) -> int:
    """Clear TOTP secret so admin re-enrolls on next login."""
    conn = _conn()
    n = conn.execute(
        "UPDATE admins SET totp_secret=NULL, totp_enrolled_at=NULL, last_used_totp_step=NULL "
        "WHERE username=?", (username,),
    ).rowcount
    if n == 0:
        print(f"no admin '{username}'", file=sys.stderr)
        return 1
    print(f"TOTP reset for '{username}' — will re-enroll on next login.")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[1]
    args = argv[2:]
    if cmd == "list-admins" and not args:
        return cmd_list()
    if cmd == "add-admin" and len(args) == 1:
        return cmd_add(args[0])
    if cmd == "remove-admin" and len(args) == 1:
        return cmd_remove(args[0])
    if cmd == "reset-totp" and len(args) == 1:
        return cmd_reset_totp(args[0])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
