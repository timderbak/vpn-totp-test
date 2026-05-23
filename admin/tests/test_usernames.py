import pytest
from pathlib import Path
from app.usernames import is_valid_username, safe_home_path, InvalidUsername


@pytest.mark.parametrize("name", ["alice", "bob", "user-1", "user_2", "a", "a" * 32])
def test_valid_usernames(name):
    assert is_valid_username(name)


@pytest.mark.parametrize("name", [
    "Alice",           # uppercase
    "1alice",          # leading digit
    "a" * 33,          # too long
    "",                # empty
    "alice/../etc",    # path traversal
    "alice space",     # space
    "../etc/passwd",
    "alice\x00",
    ".alice",
])
def test_invalid_usernames(name):
    assert not is_valid_username(name)


def test_safe_home_path_resolves_under_home(tmp_path):
    (tmp_path / "alice").mkdir()
    p = safe_home_path(str(tmp_path), "alice")
    assert p == tmp_path / "alice"


def test_safe_home_path_rejects_invalid_username(tmp_path):
    with pytest.raises(InvalidUsername):
        safe_home_path(str(tmp_path), "../etc")
