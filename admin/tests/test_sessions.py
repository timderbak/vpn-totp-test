import time
import pytest
from app.db import init_db, connect
from app.sessions import (
    create_session, lookup_session, destroy_session, touch_session,
    IDLE_TIMEOUT_SECONDS, ABSOLUTE_TIMEOUT_SECONDS, SessionInvalid,
)


@pytest.fixture
def conn(tmp_path):
    db = str(tmp_path / "a.db")
    init_db(db)
    c = connect(db)
    c.execute("INSERT INTO admins(id, username, password_hash, created_at) VALUES (1, 'a', 'h', datetime('now'))")
    return c


def test_create_and_lookup(conn):
    sid = create_session(conn, admin_id=1, ip="1.1.1.1", user_agent="ua")
    assert len(sid) == 64
    s = lookup_session(conn, sid)
    assert s.admin_id == 1


def test_destroy(conn):
    sid = create_session(conn, admin_id=1, ip=None, user_agent=None)
    destroy_session(conn, sid)
    with pytest.raises(SessionInvalid):
        lookup_session(conn, sid)


def test_unknown_session(conn):
    with pytest.raises(SessionInvalid):
        lookup_session(conn, "0" * 64)


def test_idle_timeout(conn, monkeypatch):
    sid = create_session(conn, admin_id=1, ip=None, user_agent=None)
    # backdate last_seen
    conn.execute("UPDATE sessions SET last_seen_at = datetime('now', ?) WHERE id=?",
                 (f"-{IDLE_TIMEOUT_SECONDS + 60} seconds", sid))
    with pytest.raises(SessionInvalid):
        lookup_session(conn, sid)


def test_absolute_timeout(conn):
    sid = create_session(conn, admin_id=1, ip=None, user_agent=None)
    conn.execute("UPDATE sessions SET created_at = datetime('now', ?) WHERE id=?",
                 (f"-{ABSOLUTE_TIMEOUT_SECONDS + 60} seconds", sid))
    with pytest.raises(SessionInvalid):
        lookup_session(conn, sid)
