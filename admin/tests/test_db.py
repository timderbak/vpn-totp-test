import sqlite3
from pathlib import Path
from app.db import init_db, connect


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    conn = connect(str(db_path))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )}
    expected = {"admins", "api_tokens", "enrollments", "audit_log", "sessions"}
    assert expected.issubset(tables)


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    init_db(str(db_path))  # second call must not error

    conn = connect(str(db_path))
    conn.execute("INSERT INTO admins(username, password_hash, created_at) VALUES (?, ?, datetime('now'))",
                 ("a", "h"))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1


def test_foreign_keys_enabled(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    conn = connect(str(db_path))
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
