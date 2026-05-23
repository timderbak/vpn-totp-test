import pytest
from app.db import init_db, connect
from app.audit import write_audit
from app.ratelimit import check_rate_limit, RateLimited


@pytest.fixture
def conn(tmp_path):
    db = str(tmp_path / "a.db")
    init_db(db)
    return connect(db)


def _login_fail(conn, ip):
    write_audit(conn, actor_type="anonymous", actor_id=None,
                action="login.fail", target_user=None, ip=ip,
                user_agent="t", result="fail", details=None)


def test_under_limit_passes(conn):
    for _ in range(4):
        _login_fail(conn, "1.2.3.4")
    check_rate_limit(conn, ip="1.2.3.4", action="login.fail", window_secs=900, max_count=5)


def test_over_limit_raises(conn):
    for _ in range(5):
        _login_fail(conn, "1.2.3.4")
    with pytest.raises(RateLimited) as exc:
        check_rate_limit(conn, ip="1.2.3.4", action="login.fail", window_secs=900, max_count=5)
    assert exc.value.retry_after > 0


def test_per_user_check(conn):
    for _ in range(10):
        write_audit(conn, actor_type="anonymous", actor_id=None,
                    action="login.fail", target_user="alice", ip="X",
                    user_agent="t", result="fail")
    with pytest.raises(RateLimited):
        check_rate_limit(conn, target_user="alice", action="login.fail",
                         window_secs=900, max_count=10)
