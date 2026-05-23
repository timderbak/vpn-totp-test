import json
from app.db import init_db, connect
from app.audit import write_audit, sanitize_details


def test_write_audit_persists_row(tmp_path):
    db = str(tmp_path / "a.db")
    init_db(db)
    conn = connect(db)

    write_audit(conn,
        actor_type="admin", actor_id=1,
        action="login.ok", target_user=None,
        ip="10.0.0.1", user_agent="curl/8",
        result="ok", details={"step": 1},
    )

    row = conn.execute("SELECT * FROM audit_log").fetchone()
    assert row["actor_type"] == "admin"
    assert row["action"] == "login.ok"
    assert row["result"] == "ok"
    assert json.loads(row["details"]) == {"step": 1}


def test_sanitize_redacts_known_keys():
    raw = {"password": "secret", "totp": "123456", "token": "vpa_xxx", "ok": "fine"}
    cleaned = sanitize_details(raw)
    assert cleaned == {"password": "[REDACTED]", "totp": "[REDACTED]", "token": "[REDACTED]", "ok": "fine"}


def test_sanitize_nested():
    raw = {"body": {"password": "p", "ok": 1}}
    assert sanitize_details(raw) == {"body": {"password": "[REDACTED]", "ok": 1}}
