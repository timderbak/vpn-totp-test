import sqlite3


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"rate-limited, retry after {retry_after}s")
        self.retry_after = retry_after


def check_rate_limit(
    conn: sqlite3.Connection,
    *,
    action: str,
    window_secs: int,
    max_count: int,
    ip: str | None = None,
    target_user: str | None = None,
) -> None:
    if not (ip or target_user):
        raise ValueError("must filter by ip or target_user")
    sql = (
        "SELECT COUNT(*) AS c FROM audit_log "
        "WHERE action=? AND result='fail' "
        "AND ts > datetime('now', ?) "
    )
    params: list = [action, f"-{window_secs} seconds"]
    if ip:
        sql += "AND ip=? "
        params.append(ip)
    if target_user:
        sql += "AND target_user=? "
        params.append(target_user)
    count = conn.execute(sql, params).fetchone()["c"]
    if count >= max_count:
        raise RateLimited(retry_after=window_secs)
