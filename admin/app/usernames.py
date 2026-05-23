import re
from pathlib import Path

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class InvalidUsername(ValueError):
    pass


def is_valid_username(name: str) -> bool:
    return bool(USERNAME_RE.match(name or ""))


def safe_home_path(home_dir: str, username: str) -> Path:
    if not is_valid_username(username):
        raise InvalidUsername(username)
    base = Path(home_dir).resolve()
    candidate = (base / username).resolve()
    if not candidate.is_relative_to(base):
        raise InvalidUsername(username)
    return candidate
