import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

IDLE_TIMEOUT_SECONDS = 30 * 60
ABSOLUTE_TIMEOUT_SECONDS = 12 * 60 * 60


class SessionInvalid(ValueError):
    pass


@dataclass(frozen=True)
class SessionRow:
    id: str
    admin_id: int


def create_session(conn: sqlite3.Connection, *, admin_id: int, ip: str | None, user_agent: str | None) -> str:
    sid = secrets.token_hex(32)  # 64 hex chars = 256 bits
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sessions(id, admin_id, created_at, last_seen_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, admin_id, now, now, ip, user_agent),
    )
    return sid


def lookup_session(conn: sqlite3.Connection, sid: str) -> SessionRow:
    if not sid or len(sid) != 64:
        raise SessionInvalid()
    row = conn.execute(
        "SELECT id, admin_id, "
        "  (strftime('%s','now') - strftime('%s', last_seen_at)) AS idle_secs, "
        "  (strftime('%s','now') - strftime('%s', created_at)) AS abs_secs "
        "FROM sessions WHERE id=?",
        (sid,),
    ).fetchone()
    if row is None:
        raise SessionInvalid()
    if row["idle_secs"] > IDLE_TIMEOUT_SECONDS or row["abs_secs"] > ABSOLUTE_TIMEOUT_SECONDS:
        destroy_session(conn, sid)
        raise SessionInvalid()
    return SessionRow(id=row["id"], admin_id=row["admin_id"])


def touch_session(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"), sid))


def destroy_session(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
