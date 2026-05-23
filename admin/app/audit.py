import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

SENSITIVE_KEYS = {"password", "password_hash", "totp", "totp_code", "secret",
                  "token", "plaintext_token", "scratch_code"}


def sanitize_details(details: Any) -> Any:
    if isinstance(details, dict):
        return {k: ("[REDACTED]" if k in SENSITIVE_KEYS else sanitize_details(v))
                for k, v in details.items()}
    if isinstance(details, list):
        return [sanitize_details(v) for v in details]
    return details


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    target_user: str | None,
    ip: str | None,
    user_agent: str | None,
    result: str,
    details: dict | None = None,
) -> None:
    cleaned = sanitize_details(details) if details is not None else None
    conn.execute(
        """
        INSERT INTO audit_log
            (ts, actor_type, actor_id, action, target_user, ip, user_agent, result, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            actor_type, actor_id, action, target_user, ip, user_agent, result,
            json.dumps(cleaned) if cleaned is not None else None,
        ),
    )
