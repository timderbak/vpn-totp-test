from pathlib import Path
import pytest
from app.db import init_db, connect
from app.users import (
    list_users, enroll_user, revoke_user, enable_user, UserNotFound,
)
from app.usernames import InvalidUsername


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "alice").mkdir()
    (home / "bob").mkdir()
    (home / "carol").mkdir()
    (home / "alice" / ".google_authenticator").write_text("SECRETPLACEHOLDER\n", encoding="utf-8")

    control = tmp_path / "control"
    control.mkdir()
    denylist = control / "disabled-users"
    denylist.write_text("", encoding="utf-8")

    db = str(tmp_path / "a.db")
    init_db(db)
    return {
        "home": str(home),
        "denylist": str(denylist),
        "db": db,
        "conn": connect(db),
    }


def test_list_users_reads_home_and_denylist(env):
    (Path(env["denylist"])).write_text("bob\n", encoding="utf-8")
    users = list_users(env["home"], env["denylist"], env["conn"])
    by_name = {u.username: u for u in users}
    assert set(by_name) == {"alice", "bob", "carol"}
    assert by_name["alice"].has_totp is True
    assert by_name["bob"].has_totp is False
    assert by_name["bob"].disabled is True
    assert by_name["carol"].has_totp is False
    assert by_name["carol"].disabled is False


def test_enroll_user_writes_file_and_journal(env):
    result = enroll_user(
        env["home"], env["conn"],
        username="carol", actor_type="admin", actor_id=1, issuer="ocserv-lab",
    )
    ga_file = Path(env["home"]) / "carol" / ".google_authenticator"
    assert ga_file.exists()
    assert ga_file.read_text().startswith(result.enrollment.secret)
    # journal entry recorded
    row = env["conn"].execute("SELECT action, username, actor_type, actor_id FROM enrollments").fetchone()
    assert row["action"] == "issued"
    assert row["username"] == "carol"


def test_re_enroll_records_re_issued(env):
    enroll_user(env["home"], env["conn"], username="alice", actor_type="admin", actor_id=1, issuer="x")
    row = env["conn"].execute(
        "SELECT action FROM enrollments WHERE username='alice' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["action"] == "re-issued"


def test_revoke_adds_to_denylist_and_removes_totp(env):
    revoke_user(env["home"], env["denylist"], env["conn"],
                username="alice", actor_type="admin", actor_id=1)
    denylist = Path(env["denylist"]).read_text().splitlines()
    assert "alice" in denylist
    assert not (Path(env["home"]) / "alice" / ".google_authenticator").exists()


def test_revoke_is_idempotent(env):
    revoke_user(env["home"], env["denylist"], env["conn"], username="alice",
                actor_type="admin", actor_id=1)
    revoke_user(env["home"], env["denylist"], env["conn"], username="alice",
                actor_type="admin", actor_id=1)
    lines = [l for l in Path(env["denylist"]).read_text().splitlines() if l.strip()]
    assert lines.count("alice") == 1


def test_enable_removes_from_denylist(env):
    Path(env["denylist"]).write_text("alice\nbob\n", encoding="utf-8")
    enable_user(env["home"], env["denylist"], env["conn"],
                username="bob", actor_type="admin", actor_id=1)
    lines = [l for l in Path(env["denylist"]).read_text().splitlines() if l.strip()]
    assert lines == ["alice"]


def test_enroll_rejects_unknown_user(env):
    with pytest.raises(UserNotFound):
        enroll_user(env["home"], env["conn"], username="nobody",
                    actor_type="admin", actor_id=1, issuer="x")


def test_enroll_rejects_invalid_username(env):
    with pytest.raises(InvalidUsername):
        enroll_user(env["home"], env["conn"], username="..", actor_type="admin",
                    actor_id=1, issuer="x")
